"""Requeue reel rows stranded at "Building" when the render host vanished.

WHY THIS EXISTS (2026-09-18)
----------------------------
Every September reel build ran on the self-hosted PC (`marc-pc`) — the cloud
path is skipped because the proxy probe fails, so the laptop IS the renderer.
When the laptop sleeps mid-build the runner process dies instantly and GitHub
records "The self-hosted runner lost communication with the server" with no
log at all. Five September runs died exactly that way, each within seconds of
a Windows sleep event; nine more were CANCELLED after waiting 24 h for a
runner that never came online.

The lost run is not the real damage. The damage is that the Python process
which would have written "Render Failed" was running ON the dead laptop, so
the row keeps the claim status "Building" forever — and NOTHING looks at
"Building":

  * n8n Workflow B / M only fire on "Ready to Run"
  * proxy_recovery.py only sweeps "Proxy Empty - Retry"
  * the build itself only picks up "Ready to Run"

So a row stranded this way is silently dead. That is the permanent failure
the user kept hitting: closing the lid mid-build costs the topic entirely,
and re-opening the laptop does not bring it back.

WHAT THIS DOES
--------------
Runs in GitHub's CLOUD (ubuntu-latest) on a schedule, so it works while the
laptop is off. It only touches the sheet — it never renders — so it needs no
proxy, no ffmpeg and no YouTube access (see the "requeue only" decision:
YouTube bot-blocks datacenter IPs, so cloud rendering would fail anyway).

For every row at "Building" older than STALE_AFTER_MINUTES:
  * under the attempt cap -> back to "Ready to Run" (n8n re-dispatches it, or
    the next PC build picks it up) and bump the attempt counter
  * at/over the cap      -> "Render Failed" + a note, so a genuinely broken
    row cannot ping-pong forever

Age is read from "Media Found At" / "Created Date" when present. A row whose
age cannot be determined is NOT touched on the first sighting: the recovery
writes a timestamp marker into the attempts cell instead, so the NEXT pass
(>= STALE_AFTER_MINUTES later) can age it safely. That is what keeps this
from stealing a row out from under a build that is legitimately still
running on the PC.

State lives in the "Media Status" column as a compact marker, so no new sheet
column is required:

    [requeue n=<attempts> since=<iso8601>]

Safe to run concurrently with a real build: a build that is genuinely alive
re-writes Status on every phase, and the marker's timestamp is refreshed each
time we see the row, so a slow-but-live render is never older than one pass.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

from publisher.post_generator import SheetsReader  # noqa: E402
from publisher.tweet_card_reel import (  # noqa: E402
    CLAIM_STATUS,
    _sheets_config,
    _motivation_sheets_config,
    _set_status,
    _try_update,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("stuck_row_recovery")

# A PC render takes ~5-10 min (motivation reels up to ~15 with a long
# download). 60 min is comfortably past the slowest legitimate build, so we
# never requeue a row that is still actually rendering.
STALE_AFTER_MINUTES = 60

# After this many automatic requeues, stop: the row is not failing because of
# the laptop, it is failing because of itself. Marked "Render Failed" so the
# user sees it instead of it looping through CI forever.
MAX_REQUEUE_ATTEMPTS = 3

# Requeue word: the normal GO signal, so the existing n8n gates and the
# --topic build path pick the row up with no special-casing anywhere.
REQUEUE_STATUS = "Ready to Run"
GIVE_UP_STATUS = "Render Failed"

# Where the attempt counter is stashed (no new column needed).
MARKER_COLUMN = "Media Status"
_MARKER_RE = re.compile(
    r"\[requeue n=(\d+) since=([0-9T:\-]+Z)\]", re.IGNORECASE)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_dt(raw: str) -> datetime | None:
    """Parse the timestamp formats this repo writes into the sheet.

    "Media Found At" is UTC ISO ("2026-09-15T15:18:49Z"); "Created Date" is
    SGT wall-clock ("2026-09-15 23:18:49") per the sheet contract. Anything
    unrecognised returns None so the caller falls back to the marker.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    # UTC ISO, with or without the trailing Z.
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    # "Created Date" is SGT (UTC+8) wall-clock.
    try:
        naive = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
        return naive.replace(tzinfo=timezone(timedelta(hours=8))).astimezone(
            timezone.utc)
    except ValueError:
        return None


def _read_marker(cell: str) -> tuple[int, datetime | None]:
    """(attempts, first_seen) from the marker embedded in MARKER_COLUMN."""
    m = _MARKER_RE.search(cell or "")
    if not m:
        return 0, None
    try:
        return int(m.group(1)), _parse_dt(m.group(2))
    except (ValueError, TypeError):
        return 0, None


def _write_marker(reader: SheetsReader, row_index: int, existing: str,
                  attempts: int, since: datetime) -> None:
    """Replace (or append) the marker, preserving any real Media Status text.

    The build writes genuine diagnostics here (e.g. "ALL 1 video candidate(s)
    failed to download — ..."). That text is worth keeping, so the marker is
    stripped out and re-appended rather than overwriting the cell.
    """
    body = _MARKER_RE.sub("", existing or "").strip()
    marker = f"[requeue n={attempts} since={_iso(since)}]"
    combined = f"{body} {marker}".strip() if body else marker
    _try_update(reader, row_index, MARKER_COLUMN, combined[:500])


def _building_rows(reader: SheetsReader) -> list[dict]:
    """Every row currently claimed at CLAIM_STATUS, in sheet order.

    Checks BOTH status columns: the live `Status` and the visible legacy
    `Published` (col F). n8n's claim node writes "Building" into `Published`,
    while the build writes it into `Status` — a row stranded between those two
    steps may carry the claim in only one of them.
    """
    values = reader.ws.get_all_values()
    if not values:
        return []
    headers = values[0]
    want = CLAIM_STATUS.strip().lower()
    out: list[dict] = []
    for i, raw in enumerate(values[1:], start=2):
        row = {headers[j]: raw[j] if j < len(raw) else ""
               for j in range(len(headers))}
        status = str(row.get("Status", "")).strip().lower()
        legacy = str(row.get("Published", "")).strip().lower()
        topic = str(row.get("Topic", "")).strip()
        if topic and want in (status, legacy):
            row["_row_index"] = i
            out.append(row)
    return out


def _age_minutes(row: dict, marker_since: datetime | None,
                 now: datetime) -> float | None:
    """Minutes since this row was claimed, or None if not yet knowable."""
    candidates = [
        _parse_dt(str(row.get("Media Found At", ""))),
        _parse_dt(str(row.get("Created Date", ""))),
        marker_since,
    ]
    stamps = [c for c in candidates if c is not None]
    if not stamps:
        return None
    # The most RECENT stamp is the best proxy for "when work last happened":
    # an old Media Found At on a row rebuilt today would otherwise look
    # ancient and get requeued out from under a live render.
    newest = max(stamps)
    return (now - newest).total_seconds() / 60.0


def sweep(reader: SheetsReader, tab: str, *, dry_run: bool,
          stale_minutes: int = STALE_AFTER_MINUTES) -> tuple[int, int]:
    """Requeue/expire stale rows on one tab. Returns (requeued, gave_up)."""
    now = _utcnow()
    requeued = gave_up = 0

    for row in _building_rows(reader):
        row_index = row["_row_index"]
        topic = str(row.get("Topic", "")).strip()
        existing = str(row.get(MARKER_COLUMN, ""))
        attempts, marker_since = _read_marker(existing)
        age = _age_minutes(row, marker_since, now)

        if age is None:
            # First sighting with no usable timestamp anywhere. Don't guess —
            # stamp it now so the next pass can age it honestly.
            if not dry_run:
                _write_marker(reader, row_index, existing, attempts, now)
            log.info("%s row %d %r: claimed but undated — stamped, will be "
                     "re-checked in %d min.", tab, row_index, topic,
                     stale_minutes)
            continue

        if age < stale_minutes:
            log.info("%s row %d %r: %s for %.0f min — still within the %d min "
                     "grace window, leaving it alone.", tab, row_index, topic,
                     CLAIM_STATUS, age, stale_minutes)
            continue

        if attempts >= MAX_REQUEUE_ATTEMPTS:
            log.error("%s row %d %r: stuck at %s after %d automatic requeues "
                      "— marking %s for manual review.", tab, row_index, topic,
                      CLAIM_STATUS, attempts, GIVE_UP_STATUS)
            if not dry_run:
                _set_status(reader, row_index, GIVE_UP_STATUS)
                _write_marker(
                    reader, row_index,
                    f"Gave up after {attempts} automatic requeues "
                    f"(render host kept dying mid-build).",
                    attempts, now)
            gave_up += 1
            continue

        log.warning("%s row %d %r: stranded at %s for %.0f min — requeueing "
                    "as %r (attempt %d/%d).", tab, row_index, topic,
                    CLAIM_STATUS, age, REQUEUE_STATUS, attempts + 1,
                    MAX_REQUEUE_ATTEMPTS)
        if not dry_run:
            # Marker FIRST: if the status write succeeds and this one doesn't,
            # the row would requeue forever with attempts stuck at 0.
            _write_marker(reader, row_index, existing, attempts + 1, now)
            _set_status(reader, row_index, REQUEUE_STATUS)
        requeued += 1

    return requeued, gave_up


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Requeue reel rows stranded at 'Building' by a render "
                    "host that died mid-build (laptop slept/closed).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Report what would change without writing.")
    ap.add_argument("--stale-minutes", type=int, default=STALE_AFTER_MINUTES,
                    help=f"Grace window before a claim is considered dead "
                         f"(default {STALE_AFTER_MINUTES}).")
    args = ap.parse_args()

    load_dotenv(REPO_ROOT / ".env")

    total_requeued = total_gave_up = 0
    tabs: list[tuple[str, dict]] = [
        (os.getenv("GOOGLE_SHEET_REELS_NAME", "Reels"), _sheets_config()),
    ]
    m_cfg = _motivation_sheets_config()
    if m_cfg is not None:
        tabs.append((m_cfg["google_sheets"]["sheet_name"], m_cfg))

    for tab, cfg in tabs:
        try:
            reader = SheetsReader(cfg)
        except Exception as exc:  # noqa: BLE001 — a missing tab must not abort
            log.warning("Tab %r not swept: %s", tab, exc)
            continue
        r, g = sweep(reader, tab, dry_run=args.dry_run,
                     stale_minutes=args.stale_minutes)
        total_requeued += r
        total_gave_up += g

    if args.dry_run:
        log.info("DRY RUN — no writes. Would requeue %d, give up on %d.",
                 total_requeued, total_gave_up)
    else:
        log.info("Sweep done: %d requeued, %d marked %s.",
                 total_requeued, total_gave_up, GIVE_UP_STATUS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
