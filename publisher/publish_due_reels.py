"""Publish every reel the user has APPROVED, at the Europe evening window.

WHY THIS EXISTS
---------------
The Instagram Graph API cannot truly schedule a post. We proved this against a
live post (2026-06-18): sending `scheduled_publish_time` on /media_publish is
SILENTLY IGNORED for Instagram — the reel publishes immediately. (That param
only works for Facebook Pages, not IG.)

So GitHub does the scheduling instead — WITH a human review gate (2026-08-07):

  build → "Ready to Post" (+ review email)   ← rendered, waiting for the user
  user types "Publish"                        ← approved, queued
  daily cron posts ONE per day, top-down     → "Published"

The reel build renders the video, uploads it to Drive, emails the review link,
and leaves the row at Status="Ready to Post". NOTHING publishes until the user
flips the row to "Publish" (no ED — the ED is earned). The user may type it in
either the live `Status` column or the visible legacy `Published` column F;
both are accepted, because Status sits far off-screen and col F is what's
actually on the user's screen (the 2026-08-02 gate-drift lesson).

This script runs on a daily GitHub Actions cron at 19:00 UTC = 3:00 AM MYT =
8-9 PM Central Europe (the target audience's evening peak — see the Europe
pivot, 2026-08-07). By DEFAULT it publishes ONE approved reel per run = one
post per day. If several rows say "Publish" they drain one-per-day, top-down:
row 69 today, row 70 tomorrow, and so on. (--limit 0 publishes all in one run.)

WHY IT RE-CREATES THE CONTAINER
-------------------------------
IG media containers expire ~24h after creation. A reel might render at, say,
2pm and not publish until 8pm — still fine — but one rendered yesterday would
have a dead container. Rather than depend on a stored container id that may have
expired, we re-create a fresh container from the Drive MP4 URL (column
"Reel MP4 URL"), wait for it to finish processing, then publish. Robust and
idempotent: a row already "Published" is skipped.

Run:
    python publisher/publish_due_reels.py            # publish ONE due reel (the default)
    python publisher/publish_due_reels.py --dry-run  # list due reels, post nothing
    python publisher/publish_due_reels.py --limit 0  # no cap — publish ALL due reels
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

# Status values — extend tweet_card_reel.py's state machine with a review gate.
# "Ready to Post" now means "rendered, awaiting the user's review" and is NOT
# picked up here. Only the user's explicit approval word queues a publish.
APPROVED_STATUS = "publish"         # typed by the USER: approved, queue it
PUBLISHED_STATUS = "Published"      # terminal: live on Instagram
FAILED_STATUS = "Publish Failed"    # publish attempt errored — left for retry/inspection

# The Reels tab carries TWO status-ish columns: the live `Status` (far right,
# off-screen) and the legacy `Published` at column F — the one actually visible
# on the user's screen. The user may type "Publish" in either; accept both and
# mirror every final status into col F when it's being used as a reel status
# (same rule as tweet_card_reel._set_status, 2026-08-02 gate-drift lesson).
LEGACY_STATUS_HEADER = "Published"
_REEL_STATUS_WORDS = {
    "ready to run", "building", "ready to post", "draft", "render failed",
    "proxy empty - retry", "skipped - no video",
    APPROVED_STATUS, PUBLISHED_STATUS.lower(), FAILED_STATUS.lower(),
}


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
    """Return every row the user approved: 'Publish' (trimmed, case-insensitive)
    in the live Status column OR the visible legacy col F, with a usable
    Reel MP4 URL and not already published. Sheet order = queue order, so the
    top-most approved row goes out first (row 69 today, row 70 tomorrow...)."""
    all_values = ws.get_all_values()
    if not all_values:
        return []
    headers = all_values[0]
    due: list[dict] = []
    for i, raw in enumerate(all_values[1:], start=2):
        row = {headers[j]: (raw[j] if j < len(raw) else "")
               for j in range(len(headers))}
        status = str(row.get("Status", "")).strip().lower()
        legacy = str(row.get(LEGACY_STATUS_HEADER, "")).strip().lower()
        already = str(row.get("Instagram Post", "")).strip().lower()
        mp4 = str(row.get("Reel MP4 URL", "")).strip()
        approved = APPROVED_STATUS in (status, legacy)
        if approved and mp4 and already != "published":
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


def _set_status(ws, row_index: int, value: str) -> None:
    """Write the live Status AND mirror into the visible legacy col F whenever
    that cell holds one of our reel-status words (e.g. the user's "Publish").
    A col-F value that isn't ours — a note, or the old pipeline's data — is
    left alone. Keeps whichever column the user looks at telling the truth."""
    _write(ws, row_index, "Status", value)
    try:
        headers = ws.row_values(1)
        col = headers.index(LEGACY_STATUS_HEADER) + 1
        current = str(ws.cell(row_index, col).value or "").strip()
    except Exception as exc:  # noqa: BLE001 — cosmetic; Status already written
        log.debug("Legacy %r column not mirrored: %s", LEGACY_STATUS_HEADER, exc)
        return
    if current.lower() not in _REEL_STATUS_WORDS or current == value:
        return
    _write(ws, row_index, LEGACY_STATUS_HEADER, value)


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
        _set_status(ws, row_index, FAILED_STATUS)
        return False
    except Exception as exc:  # noqa: BLE001
        log.error("Row %d publish error: %s", row_index, exc)
        _write(ws, row_index, "Instagram Post", f"Publish error: {exc}"[:200])
        _set_status(ws, row_index, FAILED_STATUS)
        return False

    permalink = fetch_permalink(media_id, access_token)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write(ws, row_index, "Instagram Post ID", media_id)
    _write(ws, row_index, "Instagram Post", "Published")
    _write(ws, row_index, "Post URL", permalink)
    _write(ws, row_index, "Published Date", now)
    _set_status(ws, row_index, PUBLISHED_STATUS)
    log.info("Row %d PUBLISHED -> %s", row_index, permalink or media_id)
    return True



def _token_alive(access_token: str) -> tuple[bool, str]:
    """Read-only pre-flight: is the IG token still valid? (Graph debug_token.)

    WHY (2026-08-24): the long-lived token silently expired on 18-Aug-26 and
    every nightly run went GREEN while publishing 0/1 — the script caught the
    OAuthException, marked the approved row "Publish Failed", and moved on.
    Four approved reels were stranded over a week. A dead token is NOT the
    row's fault, so we now refuse to touch the sheet at all: abort loudly
    (non-zero exit → red run) and email a token-specific alert instead.
    """
    import requests  # late import
    try:
        r = requests.get(
            "https://graph.facebook.com/v21.0/debug_token",
            params={"input_token": access_token, "access_token": access_token},
            timeout=30,
        )
        js = r.json()
    except Exception as exc:  # noqa: BLE001 — network blip: don't block a publish
        log.warning("Token pre-flight could not run (%s) — continuing.", exc)
        return True, ""
    err = js.get("error") or {}
    data = js.get("data") or {}
    if err.get("code") == 190 or (data and not data.get("is_valid", True)):
        return False, err.get("message") or json.dumps(data)[:300]
    exp = data.get("expires_at")
    if exp:
        days = (datetime.fromtimestamp(int(exp), tz=timezone.utc)
                - datetime.now(timezone.utc)).days
        log.info("Token OK — expires in ~%d day(s).", days)
        if days <= 7:
            log.warning("Token expires in %d day(s) — refresh_ig_token.yml "
                        "should renew it; check its last run.", days)
    return True, ""


def _alert_token_dead(reason: str) -> None:
    """Email: the token is dead, NOTHING will publish until it's replaced."""
    try:
        from publisher.notify_email import send  # late import
        send(
            "[GenZ ALERT] Instagram token EXPIRED — auto-publish is STOPPED",
            "The 3am MYT auto-publish run aborted before touching any row.\n\n"
            f"Reason: {reason}\n\n"
            "Approved rows are left as 'Publish' and will go out automatically "
            "once the token is fixed. To fix: generate a new long-lived token "
            "for app 'Gen Z publisher' (989601526736983), then update the "
            "INSTAGRAM_ACCESS_TOKEN GitHub secret. See "
            "publisher/workflows/publish_instagram_post.md -> 'Access token'.\n",
        )
        log.info("Token-expired alert email sent.")
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not send token alert email: %s", exc)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="List due reels but publish nothing.")
    parser.add_argument("--limit", type=int, default=1,
                        help="Max reels to publish this run. Default 1 = one post "
                             "per day; pass 0 for no cap (publish all due reels).")
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

    alive, reason = _token_alive(access_token)
    if not alive:
        log.error("Instagram token is DEAD — aborting before touching any row: %s",
                  reason)
        if not args.dry_run:
            _alert_token_dead(reason)
        return 2

    from publisher.post_generator import SheetsReader  # late import: needs gspread
    reader = SheetsReader(_config())
    ws = reader.ws

    due = _find_due_rows(ws)
    if not due:
        log.info("No approved reels (no row says 'Publish'). Nothing to post.")
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
    """Email the user that one or more approved reels failed to publish."""
    try:
        from publisher.notify_email import send  # late import
        lines = "\n".join(f"  - {t}" for t in failed)
        subject = f"[GenZ ALERT] {len(failed)}/{total} reel(s) failed to publish"
        body = (
            "The 3am MYT (Europe evening) auto-publish run hit problems.\n\n"
            f"Published OK: {ok}/{total}\n"
            f"FAILED: {len(failed)}\n{lines}\n\n"
            "These rows are now marked 'Publish Failed' in the sheet — they "
            "will NOT auto-retry. Open the sheet to see the error in the "
            "'Instagram Post' column. To retry, set the row back to "
            "'Publish' and it'll go out at the next 3am MYT run.\n"
        )
        send(subject, body)
        log.info("Failure-alert email sent (%d failed).", len(failed))
    except Exception as exc:  # noqa: BLE001 — alerting must never crash the run
        log.warning("Could not send failure-alert email: %s", exc)


if __name__ == "__main__":
    raise SystemExit(main())
