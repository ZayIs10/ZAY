"""Publish every reel that's queued and DUE, at peak Singapore/Malaysia time.

WHY THIS EXISTS
---------------
The Instagram Graph API cannot truly schedule a post. We proved this against a
live post (2026-06-18): sending `scheduled_publish_time` on /media_publish is
SILENTLY IGNORED for Instagram — the reel publishes immediately. (That param
only works for Facebook Pages, not IG.)

So GitHub does the scheduling instead. The reel build renders the video, uploads
it to Drive, and leaves the row at Status="Ready to Post". This script is run by
a daily GitHub Actions cron at 12:00 UTC = 8:00 PM SGT (the peak SG/MY evening
window). It finds every "Ready to Post" row and publishes it NOW — so from the
user's side, reels queue up whenever they render and go live at 8pm SGT.

WHY IT RE-CREATES THE CONTAINER
-------------------------------
IG media containers expire ~24h after creation. A reel might render at, say,
2pm and not publish until 8pm — still fine — but one rendered yesterday would
have a dead container. Rather than depend on a stored container id that may have
expired, we re-create a fresh container from the Drive MP4 URL (column
"Reel MP4 URL"), wait for it to finish processing, then publish. Robust and
idempotent: a row already "Published" is skipped.

Run:
    python publisher/publish_due_reels.py            # publish all due reels
    python publisher/publish_due_reels.py --dry-run  # list due reels, post nothing
    python publisher/publish_due_reels.py --limit 3  # cap how many go out
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("publish_due_reels")

# Status values — mirror tweet_card_reel.py's state machine.
READY_STATUS = "ready to post"      # queued by the build, waiting for peak time
PUBLISHED_STATUS = "Published"      # terminal: live on Instagram
FAILED_STATUS = "Publish Failed"    # publish attempt errored — left for retry/inspection


def _config() -> dict:
    """Sheet config in the shape SheetsReader expects (matches tweet_card_reel)."""
    return {
        "google_sheets": {
            "credentials_file": "google_service_account.json",
            "spreadsheet_id": os.getenv("GOOGLE_SHEET_ID", ""),
            "sheet_name": os.getenv("GOOGLE_SHEET_REELS_NAME", "Reels"),
        }
    }


def _find_due_rows(ws) -> list[dict]:
    """Return every row whose Status == 'Ready to Post' (trimmed, case-insensitive),
    that has a usable Reel MP4 URL and isn't already published."""
    all_values = ws.get_all_values()
    if not all_values:
        return []
    headers = all_values[0]
    due: list[dict] = []
    for i, raw in enumerate(all_values[1:], start=2):
        row = {headers[j]: (raw[j] if j < len(raw) else "")
               for j in range(len(headers))}
        status = str(row.get("Status", "")).strip().lower()
        already = str(row.get("Instagram Post", "")).strip().lower()
        mp4 = str(row.get("Reel MP4 URL", "")).strip()
        if status == READY_STATUS and mp4 and already != "published":
            row["_row_index"] = i
            due.append(row)
    return due


def _drive_direct_url(url: str) -> str:
    """Instagram must fetch the MP4 over HTTP. A Drive 'uc?export=download' URL
    works; a '/file/d/<id>/view' share URL does not. Normalize to the direct
    download form when we can spot a file id."""
    url = url.strip()
    if "drive.google.com" not in url:
        return url
    file_id = ""
    if "/file/d/" in url:
        file_id = url.split("/file/d/", 1)[1].split("/", 1)[0]
    elif "id=" in url:
        file_id = url.split("id=", 1)[1].split("&", 1)[0]
    if file_id:
        return f"https://drive.google.com/uc?export=download&id={file_id}"
    return url


def _write(ws, row_index: int, header: str, value: str) -> None:
    """Best-effort single-cell update by header name (no-op if column absent)."""
    try:
        headers = ws.row_values(1)
        col = headers.index(header) + 1
        ws.update_cell(row_index, col, value)
    except Exception as exc:  # noqa: BLE001 — a sheet write must not abort the run
        log.warning("Could not write %r to row %d: %s", header, row_index, exc)


def publish_one(ws, row: dict, ig_user_id: str, access_token: str,
                *, dry_run: bool) -> bool:
    """Publish a single due reel. Returns True on success."""
    from publisher.publish_reel import (  # late import: needs requests
        create_reel_container,
        wait_for_container,
        publish_container,
        fetch_permalink,
    )

    row_index = row["_row_index"]
    topic = (row.get("Topic") or "").strip()
    caption = (row.get("Post Caption") or "").strip()
    video_url = _drive_direct_url(row.get("Reel MP4 URL") or "")

    log.info("Row %d DUE: %r", row_index, topic)
    if dry_run:
        log.info("  DRY RUN — would publish from %s", video_url)
        return True

    try:
        container_id = create_reel_container(
            ig_user_id, access_token, video_url, caption)
        wait_for_container(container_id, access_token)
        media_id = publish_container(ig_user_id, access_token, container_id)
    except SystemExit as exc:
        # publish_reel.py uses sys.exit() for API errors — catch so one bad row
        # doesn't kill the whole batch.
        log.error("Row %d publish failed: %s", row_index, exc)
        _write(ws, row_index, "Instagram Post", f"Publish failed: {exc}"[:200])
        _write(ws, row_index, "Status", FAILED_STATUS)
        return False
    except Exception as exc:  # noqa: BLE001
        log.error("Row %d publish error: %s", row_index, exc)
        _write(ws, row_index, "Instagram Post", f"Publish error: {exc}"[:200])
        _write(ws, row_index, "Status", FAILED_STATUS)
        return False

    permalink = fetch_permalink(media_id, access_token)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write(ws, row_index, "Instagram Post ID", media_id)
    _write(ws, row_index, "Instagram Post", "Published")
    _write(ws, row_index, "Post URL", permalink)
    _write(ws, row_index, "Published Date", now)
    _write(ws, row_index, "Status", PUBLISHED_STATUS)
    log.info("Row %d PUBLISHED -> %s", row_index, permalink or media_id)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="List due reels but publish nothing.")
    parser.add_argument("--limit", type=int, default=0,
                        help="Max reels to publish this run (0 = no cap).")
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")
    access_token = os.getenv("INSTAGRAM_ACCESS_TOKEN")
    ig_user_id = os.getenv("INSTAGRAM_IG_USER_ID")
    if not access_token or not ig_user_id:
        log.error("INSTAGRAM_ACCESS_TOKEN / INSTAGRAM_IG_USER_ID not set — abort.")
        return 1
    if not os.getenv("GOOGLE_SHEET_ID"):
        log.error("GOOGLE_SHEET_ID not set — abort.")
        return 1

    from publisher.post_generator import SheetsReader  # late import: needs gspread
    reader = SheetsReader(_config())
    ws = reader.ws

    due = _find_due_rows(ws)
    if not due:
        log.info("No reels are due (no 'Ready to Post' rows). Nothing to publish.")
        return 0

    if args.limit and len(due) > args.limit:
        log.info("%d due, capping to --limit %d.", len(due), args.limit)
        due = due[: args.limit]

    log.info("%d reel(s) due for publishing.", len(due))
    ok = 0
    failed: list[str] = []
    for row in due:
        if publish_one(ws, row, ig_user_id, access_token, dry_run=args.dry_run):
            ok += 1
        else:
            failed.append((row.get("Topic") or f"row {row['_row_index']}").strip())
    log.info("Done. %d/%d published%s.", ok, len(due),
             " (dry run)" if args.dry_run else "")

    # If any reel failed to publish, email the user so a stranded reel is never
    # silent. Best-effort — a notify failure must not change the exit behavior.
    # (Stranded reels stay "Ready to Post"... wait, failures are marked
    # "Publish Failed", so they won't silently retry — the email is the signal
    # to look. Dry runs never alert.)
    if failed and not args.dry_run:
        _alert_failures(failed, ok, len(due))

    return 0


def _alert_failures(failed: list[str], ok: int, total: int) -> None:
    """Email the user that one or more reels failed to publish at 8pm SGT."""
    try:
        from publisher.notify_email import send  # late import
        lines = "\n".join(f"  - {t}" for t in failed)
        subject = f"[GenZ ALERT] {len(failed)}/{total} reel(s) failed to publish"
        body = (
            "The 8pm SGT auto-publish run hit problems.\n\n"
            f"Published OK: {ok}/{total}\n"
            f"FAILED: {len(failed)}\n{lines}\n\n"
            "These rows are now marked Status='Publish Failed' in the sheet — "
            "they will NOT auto-retry. Open the sheet to see the error in the "
            "'Instagram Post' column. To retry, set the row's Status back to "
            "'Ready to Post' and it'll go out at the next 8pm SGT.\n"
        )
        send(subject, body)
        log.info("Failure-alert email sent (%d failed).", len(failed))
    except Exception as exc:  # noqa: BLE001 — alerting must never crash the run
        log.warning("Could not send failure-alert email: %s", exc)


if __name__ == "__main__":
    raise SystemExit(main())
