"""Publish every reel the user has APPROVED, at the Europe evening window.

WHY THIS EXISTS
---------------
The Instagram Graph API cannot truly schedule a post. We proved this against a
live post (2026-06-18): sending `scheduled_publish_time` on /media_publish is
SILENTLY IGNORED for Instagram — the reel publishes immediately. (That param
only works for Facebook Pages, not IG.)

So GitHub does the scheduling instead — WITH a human review gate (2026-08-07):

  build → "Ready to Post" (+ review email)   ← rendered, waiting for the user
  user types "Approved"                       ← approved, queued
  daily cron posts ONE per day, top-down     → "Published"

The reel build renders the video, uploads it to Drive, emails the review link,
and leaves the row at Status="Ready to Post". NOTHING publishes until the user
flips the row to "Approved". The user may type it in either the live `Status`
column or the visible legacy `Published` column F; both are accepted, because
Status sits far off-screen and col F is what's actually on the user's screen
(the 2026-08-02 gate-drift lesson).

THE APPROVAL WORD CHANGED 2026-09-18 ("Publish" -> "Approved")
--------------------------------------------------------------
The old word was "Publish" — one ED away from the terminal "Published", in the
SAME column that n8n's build gate reads. That collision bit for real: the live
Workflow B had an IF node matching Published == "Publish" wired into the BUILD
path, so typing the approval word dispatched a render, and the claim node then
wrote "Building" over the cell. The approval vanished about a second after it
was typed; rows 84 and 85 were approved on 15-Sep and silently never published
while every nightly run exited green with nothing to do.

"Approved" shares no prefix with "Building" or "Published", so no future gate
can confuse them. "Publish" is STILL ACCEPTED so rows approved before the
change keep working — only what we ASK for changed, never what we understand.

This script runs on a daily GitHub Actions cron at 19:00 UTC = 3:00 AM MYT =
8-9 PM Central Europe (the target audience's evening peak — see the Europe
pivot, 2026-08-07). By DEFAULT it publishes ONE approved reel per run = one
post per day. If several rows say "Approved" they drain one-per-day, top-down:
row 69 today, row 70 tomorrow, and so on. (--limit 0 publishes all in one run.)

WHY IT RE-CREATES THE CONTAINER
-------------------------------
IG media containers expire ~24h after creation. A reel might render at, say,
2pm and not publish until 8pm — still fine — but one rendered yesterday would
have a dead container. Rather than depend on a stored container id that may have
expired, we re-create a fresh container from the Drive MP4 URL (column
"Reel MP4 URL"), wait for it to finish processing, then publish. Robust and
idempotent: a row already "Published" is skipped.

WHY IT EMAILS BEFORE *AND* AFTER (2026-09-16)
-------------------------------------------
The Reels tab and the Motivation tab both feed this ONE scheduler, and the
user cannot watch a 3 AM cron. So the run is never silent:
  * HEADS-UP (--heads-up, cron at 07:00 UTC = 3:00 PM MYT, 12 h ahead):
    "this reel is PLANNED to publish tonight at 3:00 AM MYT" — which tab,
    which row, the Drive link, the caption, and what else is queued behind
    it. Read-only: nothing is written to the sheet. If the token is dead it
    says so NOW, while there is still time to fix it. No queue = no email.
  * CONFIRMATION (after a real publish): "PUBLISHED on Instagram" + post URL.
    Together with the existing failure alerts, every planned post ends in
    exactly one of: published-email, failed-email, or the token-dead email.

Run:
    python publisher/publish_due_reels.py            # publish ONE due reel (the default)
    python publisher/publish_due_reels.py --heads-up # email what is PLANNED tonight, post nothing
    python publisher/publish_due_reels.py --dry-run  # list due reels, post nothing, no email
    python publisher/publish_due_reels.py --limit 0  # no cap — publish ALL due reels
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
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
# The approval word the user types. Changed from "Publish" to "Approved" on
# 2026-09-18 after a real collision: the live n8n Workflow B had an IF node
# gating on Published == "Publish" wired into the BUILD path, so typing the
# approval word fired a render AND the claim node overwrote the cell with
# "Building" — erasing the approval about a second after it was typed. Rows 84
# and 85 were approved on 15-Sep and never published; the nightly run kept
# exiting green with nothing to do.
#
# "Approved" shares no prefix with "Building" or "Published", so no gate or
# status check can confuse them again. "publish" is still ACCEPTED (below) so
# any row the user already typed keeps working — the word only changed for
# what we ASK for, never for what we understand.
APPROVED_STATUS = "approved"        # typed by the USER: approved, queue it
LEGACY_APPROVED_STATUS = "publish"  # still honoured: pre-2026-09-18 rows
APPROVED_WORDS = frozenset({APPROVED_STATUS, LEGACY_APPROVED_STATUS})
PUBLISHED_STATUS = "Published"      # terminal: live on Instagram
FAILED_STATUS = "Publish Failed"    # publish attempt errored — left for retry/inspection

# The Reels tab carries TWO status-ish columns: the live `Status` (far right,
# off-screen) and the legacy `Published` at column F — the one actually visible
# on the user's screen. The user may type "Publish" in either; accept both and
# mirror every final status into col F when it's being used as a reel status
# (same rule as tweet_card_reel._set_status, 2026-08-02 gate-drift lesson).
# The publish cron (publish_due_reels.yml): 19:00 UTC daily = 03:00 MYT.
# Kept here so the emails state the real slot instead of a hard-coded phrase.
PUBLISH_UTC_HOUR = 19
_MYT = timezone(timedelta(hours=8), "MYT")


def next_publish_slot(now: datetime | None = None) -> datetime:
    """The next 19:00 UTC strictly after `now` (UTC-aware)."""
    now = now or datetime.now(timezone.utc)
    slot = now.replace(hour=PUBLISH_UTC_HOUR, minute=0, second=0, microsecond=0)
    if slot <= now:
        slot += timedelta(days=1)
    return slot


def _clock(dt: datetime) -> str:
    """'3:00 AM' — %I keeps a leading zero on every platform, so strip it."""
    return dt.strftime("%I:%M %p").lstrip("0")


def _slot_label(slot: datetime) -> str:
    """'Wed 17 Sep 2026, 3:00 AM MYT (9:00 PM Europe, 19:00 UTC)'."""
    myt = slot.astimezone(_MYT)
    europe = ""
    try:
        from zoneinfo import ZoneInfo  # tzdata may be missing on some hosts
        europe = f"{_clock(slot.astimezone(ZoneInfo('Europe/Berlin')))} Europe, "
    except Exception:  # noqa: BLE001 — cosmetic
        pass
    return (f"{myt.strftime('%a %d %b %Y')}, {_clock(myt)} MYT "
            f"({europe}{slot.strftime('%H:%M')} UTC)")


LEGACY_STATUS_HEADER = "Published"
_REEL_STATUS_WORDS = {
    "ready to run", "building", "ready to post", "draft", "render failed",
    "proxy empty - retry", "skipped - no video",
    APPROVED_STATUS, LEGACY_APPROVED_STATUS,
    PUBLISHED_STATUS.lower(), FAILED_STATUS.lower(),
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


def _motivation_config() -> dict | None:
    """Same spreadsheet, the Motivation tab — speech reels live on their own
    tab since 2026-09-04. None when the tab name is explicitly blanked."""
    name = os.getenv("GOOGLE_SHEET_MOTIVATION_NAME", "Motivation").strip()
    if not name:
        return None
    cfg = _config()
    cfg["google_sheets"]["sheet_name"] = name
    return cfg


def _find_due_rows(ws) -> list[dict]:
    """Return every row the user approved: 'Approved' (or the pre-2026-09-18
    'Publish'), trimmed and case-insensitive, in the live Status column OR the
    visible legacy col F, with a usable Reel MP4 URL and not already published.
    Sheet order = queue order, so the top-most approved row goes out first
    (row 69 today, row 70 tomorrow...)."""
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
        approved = bool(APPROVED_WORDS & {status, legacy})
        if approved and mp4 and already != "published":
            row["_row_index"] = i
            row["_tab"] = getattr(ws, "title", "") or ""
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
    row["_permalink"] = permalink or f"media id {media_id}"
    row["_published_at"] = now
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
            "Approved rows are left as 'Approved' and will go out automatically "
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
    parser.add_argument("--heads-up", action="store_true",
                        help="Email what is PLANNED for the next publish slot "
                             "(tab, row, caption, queue) and post nothing. "
                             "Read-only; sends nothing when the queue is empty.")
    parser.add_argument("--limit", type=int, default=1,
                        help="Max reels to publish this run. Default 1 = one post "
                             "per day; pass 0 for no cap (publish all due reels).")
    args = parser.parse_args()
    if args.heads_up:
        args.dry_run = True  # a heads-up never posts

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
    if not alive and not args.heads_up:
        log.error("Instagram token is DEAD — aborting before touching any row: %s",
                  reason)
        if not args.dry_run:
            _alert_token_dead(reason)
        return 2
    if not alive:
        # Heads-up mode keeps going: the whole point is to warn 12 h early.
        log.error("Instagram token is DEAD — tonight's publish WILL fail: %s",
                  reason)

    from publisher.post_generator import SheetsReader  # late import: needs gspread

    # Approved reels can live on TWO tabs since 2026-09-04: the AI-content
    # Reels tab and the Motivation speech tab. Scan both (Reels first — sheet
    # order stays queue order per tab); the combined queue still honors
    # --limit, so the default is ONE post per day across both tabs.
    due: list[tuple] = []
    for cfg, required in ((_config(), True), (_motivation_config(), False)):
        if cfg is None:
            continue
        try:
            ws = SheetsReader(cfg).ws
        except Exception as exc:  # noqa: BLE001 — Motivation tab may not exist
            if required:
                raise
            log.info("Tab %r not readable (%s) — skipping it.",
                     cfg["google_sheets"]["sheet_name"], exc)
            continue
        due += [(ws, row) for row in _find_due_rows(ws)]

    if not due:
        log.info("No approved reels (no row says 'Approved'). Nothing to post%s.",
                 " — no heads-up email" if args.heads_up else "")
        return 0

    if args.heads_up:
        rows = [row for _ws, row in due]
        tonight = rows[: args.limit] if args.limit else rows
        _send_heads_up(tonight, rows[len(tonight):],
                       token_problem="" if alive else reason)
        return 0

    if args.limit and len(due) > args.limit:
        log.info("%d due, capping to --limit %d.", len(due), args.limit)
        due = due[: args.limit]

    log.info("%d reel(s) due for publishing.", len(due))
    ok = 0
    failed: list[str] = []
    published: list[dict] = []
    for ws, row in due:
        if publish_one(ws, row, ig_user_id, access_token, dry_run=args.dry_run):
            ok += 1
            published.append(row)
        else:
            failed.append((row.get("Topic") or f"row {row['_row_index']}").strip())
    log.info("Done. %d/%d published%s.", ok, len(due),
             " (dry run)" if args.dry_run else "")

    # Confirmation: the user asked (2026-09-16) never to have to guess whether
    # a planned reel actually went live. Best-effort; dry runs never email.
    if published and not args.dry_run:
        _send_published(published)

    # If any reel failed to publish, email the user so a stranded reel is never
    # silent. Best-effort — a notify failure must not change the exit behavior.
    # (Stranded reels stay "Ready to Post"... wait, failures are marked
    # "Publish Failed", so they won't silently retry — the email is the signal
    # to look. Dry runs never alert.)
    if failed and not args.dry_run:
        _alert_failures(failed, ok, len(due))

    return 0


def _describe(row: dict, *, with_caption: bool) -> str:
    """One queued reel, for the emails: tab/row/topic/Drive link (+ caption)."""
    topic = (row.get("Topic") or "").strip() or "(no topic)"
    tab = row.get("_tab") or "?"
    kind = "Motivation speech reel" if tab.lower().startswith("motiv") else "Reel"
    lines = [
        f"{topic}",
        f"  Tab: {tab} (row {row.get('_row_index')})  ·  Type: {kind}",
        f"  Video: {(row.get('Reel MP4 URL') or '').strip() or '(no Drive link)'}",
    ]
    if with_caption:
        cap = (row.get("Post Caption") or "").strip() or "(no caption in sheet)"
        lines += ["  Caption that will be posted:", "  " + cap.replace("\n", "\n  ")]
    return "\n".join(lines)


def _send_heads_up(tonight: list[dict], later: list[dict], *,
                   token_problem: str = "") -> None:
    """Email: these reels are PLANNED to publish at the next slot.

    Sent by the 3:00 PM MYT run — 12 h before the 3:00 AM MYT publish — so
    the user knows in advance what will go live and can still pull a row
    (delete the word "Approved") if they change their mind. Read-only.
    """
    try:
        from publisher.notify_email import send  # late import
        when = _slot_label(next_publish_slot())
        n = len(tonight)
        first = (tonight[0].get("Topic") or "").strip() if tonight else ""
        subject = (f"[GenZ] PLANNED: {first} publishes to Instagram at {when}"
                   if n == 1 else
                   f"[GenZ] PLANNED: {n} reels publish to Instagram at {when}")
        if token_problem:
            subject = "[GenZ WARNING] Instagram token is DEAD — " + subject[7:]
        parts = [
            f"PLANNED INSTAGRAM PUBLISH — {when}",
            "=" * 60,
            "Nothing has been posted yet. This is the 12-hour heads-up from the",
            "scheduler: at the time above it will publish the reel(s) below",
            "(one per day, top row first; Reels tab before Motivation tab).",
            "",
            "To STOP a reel from going out: open the sheet and clear the word",
            "\"Approved\" from that row before the time above. To let it run: do",
            "nothing. You will get a second email once it is actually live.",
            "",
        ]
        if token_problem:
            parts += [
                "!! WARNING: the Instagram token is INVALID right now, so tonight's",
                f"!! publish WILL FAIL unless it is replaced first: {token_problem}",
                "!! Fix: publisher/workflows/publish_instagram_post.md -> Access token.",
                "",
            ]
        parts += ["GOING LIVE AT THAT TIME:", "-" * 60]
        parts += [_describe(r, with_caption=True) + "\n" for r in tonight]
        if later:
            parts += ["QUEUED FOR THE FOLLOWING DAYS (one per day, in this order):",
                      "-" * 60]
            parts += [f"{i}. " + _describe(r, with_caption=False).replace(
                "\n", "\n   ") for i, r in enumerate(later, start=1)]
        send(subject, "\n".join(parts) + "\n")
        log.info("Heads-up email sent (%d tonight, %d queued later).", n, len(later))
    except Exception as exc:  # noqa: BLE001 — a notify failure must never crash
        log.warning("Could not send heads-up email: %s", exc)


def _send_published(published: list[dict]) -> None:
    """Email: the planned reel(s) are now LIVE on Instagram (with the URL)."""
    try:
        from publisher.notify_email import send  # late import
        n = len(published)
        first = (published[0].get("Topic") or "").strip()
        subject = (f"[GenZ] PUBLISHED on Instagram: {first}" if n == 1
                   else f"[GenZ] PUBLISHED on Instagram: {n} reels")
        parts = ["The scheduled publish ran and these reels are LIVE:", ""]
        for r in published:
            parts += [
                _describe(r, with_caption=False),
                f"  Instagram: {r.get('_permalink', '')}",
                f"  Published at: {r.get('_published_at', '')} (UTC)",
                "",
            ]
        parts += ["The sheet row(s) now say \"Published\" with the post URL and",
                  "date. Nothing else is needed."]
        send(subject, "\n".join(parts) + "\n")
        log.info("Published-confirmation email sent (%d reel(s)).", n)
    except Exception as exc:  # noqa: BLE001 — never fail a successful publish
        log.warning("Could not send published-confirmation email: %s", exc)


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
            "'Approved' and it'll go out at the next 3am MYT run.\n"
        )
        send(subject, body)
        log.info("Failure-alert email sent (%d failed).", len(failed))
    except Exception as exc:  # noqa: BLE001 — alerting must never crash the run
        log.warning("Could not send failure-alert email: %s", exc)


if __name__ == "__main__":
    raise SystemExit(main())
