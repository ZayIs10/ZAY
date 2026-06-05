"""Build one @execute-style tweet-card reel for a Reels-tab row.

This is the entry script the GitHub Actions render worker invokes
(see .github/workflows/build_tweet_card_reel.yml). It can also be
run locally for dry-runs.

Pipeline:
  1. Read the row from the Reels tab.
  2. Validate Post Caption + Media Video URL + Media Image URL.
  3. Download the source video (yt-dlp / HTTP).
  4. Download the poster image.
  5. Render the tweet-card PNG (publisher/tweet_card.py).
  6. Composite the final mp4 (publisher/compositor.py).
  7. Upload to Drive (reuse publisher/publish_reel.upload_to_drive).
  8. Write Reel MP4 URL + Status="Ready to Post" back to the row.

If anything fails: row Status is set to "Render Failed" with the
error truncated into the Media Status cell for debugging.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from publisher.post_generator import SheetsReader  # noqa: E402
from publisher.media_consumer import fetch_single_clip, fetch_image  # noqa: E402
from publisher.tweet_card import render as render_card  # noqa: E402
from publisher.compositor import build as composite_reel  # noqa: E402

RENDERS_DIR = REPO_ROOT / "renders"
TMP_DIR = REPO_ROOT / ".tmp" / "tweet_card_reel"
LOGO_PATH = REPO_ROOT / "logo.png"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("tweet_card_reel")


# ---------------------------------------------------------------------------

def _slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:40] or "reel"


def _sheets_config() -> dict:
    load_dotenv(REPO_ROOT / ".env")
    sheet_id = os.getenv("GOOGLE_SHEET_ID")
    if not sheet_id:
        raise RuntimeError("GOOGLE_SHEET_ID is not set in .env")
    return {
        "google_sheets": {
            "credentials_file": "google_service_account.json",
            "spreadsheet_id": sheet_id,
            "sheet_name": os.getenv("GOOGLE_SHEET_REELS_NAME", "Reels"),
        }
    }


def _read_row_by_index(reader: SheetsReader, row_index: int) -> dict:
    """Read a single row by 1-indexed sheet row number."""
    all_values = reader.ws.get_all_values()
    if row_index < 2 or row_index > len(all_values):
        raise RuntimeError(f"Row {row_index} out of range")
    headers = all_values[0]
    raw = all_values[row_index - 1]
    row = {headers[j]: raw[j] if j < len(raw) else "" for j in range(len(headers))}
    row["_row_index"] = row_index
    return row


# "Ready to Run" is the explicit GO signal the user sets. It is deliberately
# DIFFERENT from the terminal "Ready to Post" so the build never re-triggers
# itself into an infinite loop. Matched case-insensitively + trimmed because
# the value is hand-typed in the sheet ("ready to run ", "Ready to Run", ...).
TRIGGER_STATUS = "ready to run"
CLAIM_STATUS = "Building"          # set immediately so a re-poll won't double-fire
DONE_STATUS = "Ready to Post"      # terminal: reel is in Drive, do NOT re-build
SKIPPED_STATUS = "Skipped - No Video"  # terminal: no real clip found, post skipped


class NoVideoError(RuntimeError):
    """Raised when no usable source video clip could be found/downloaded.

    The user's rule: a reel MUST use real footage. If there's no clip, we
    SKIP the post rather than ship a still. The runner maps this to the
    terminal SKIPPED_STATUS (not a hard failure / not a retry)."""


def _find_ready_to_run_row(reader: SheetsReader) -> dict | None:
    """Return the first row whose Status is 'Ready to Run' (trimmed,
    case-insensitive) AND has a Topic, or None."""
    all_values = reader.ws.get_all_values()
    if not all_values:
        return None
    headers = all_values[0]
    for i, raw in enumerate(all_values[1:], start=2):
        row = {headers[j]: raw[j] if j < len(raw) else "" for j in range(len(headers))}
        status = str(row.get("Status", "")).strip().lower()
        topic = str(row.get("Topic", "")).strip()
        if status == TRIGGER_STATUS and topic:
            row["_row_index"] = i
            return row
    return None


def _try_update(reader: SheetsReader, row_index: int, header: str, value: str) -> None:
    """Best-effort cell update — log + skip if the column doesn't exist."""
    try:
        col = reader._col_index(header)
        reader.ws.update_cell(row_index, col, value)
    except (ValueError, IndexError) as exc:
        log.warning("Could not update column %r: %s", header, exc)


def _mark_failed(reader: SheetsReader, row_index: int, msg: str) -> None:
    _try_update(reader, row_index, "Status", "Render Failed")
    _try_update(reader, row_index, "Media Status", msg[:200])


def _ensure_media(reader: SheetsReader, row_index: int, row: dict,
                  *, dry_run: bool) -> dict:
    """If the row is missing a video/image URL, discover them (keyless) and
    write them back, then return the refreshed row. No-op on failure — the
    caller still validates and will mark the row failed if media is absent."""
    topic = (row.get("Topic") or "").strip()
    if not topic:
        return row
    log.info("Media missing on row %d — running keyless finder...", row_index)
    try:
        from publisher import media_finder  # noqa: E402
        result = media_finder.discover_for_topic(
            topic, row.get("Key Points") or "",
        )
        if not dry_run:
            media_finder.write_row_media(reader.ws, row_index, result)
            row = _read_row_by_index(reader, row_index)
        else:  # dry run: graft winners onto the in-memory row only
            v = (result["video"]["winner"] or {}).get("media_url", "")
            i = (result["image"]["winner"] or {}).get("media_url", "")
            if v:
                row["Media Video URL"] = v
            if i:
                row["Media Image URL"] = i
        log.info("Media found -> video=%s image=%s",
                 bool(row.get("Media Video URL", "").strip()),
                 bool(row.get("Media Image URL", "").strip()))
    except Exception as exc:  # noqa: BLE001 — finder is best-effort
        log.warning("Auto media-find failed on row %d: %s", row_index, exc)
    return row


# ---------------------------------------------------------------------------

def build_reel_for_row(row: dict) -> Path:
    """Pure build step — no Sheet I/O. Returns the local mp4 path.

    Kept separate from the sheet-update wrapper so it's straightforward
    to invoke from a notebook / test against a hand-built row dict.
    """
    row_index = row["_row_index"]
    topic = (row.get("Topic") or "").strip()
    caption = (row.get("Post Caption") or "").strip()
    video_url = (row.get("Media Video URL") or "").strip()
    image_url = (row.get("Media Image URL") or "").strip()

    # A reel MUST use real footage. Topic + Caption + poster image + a usable
    # source VIDEO are all required. If no clip can be found/downloaded, the
    # post is SKIPPED (NoVideoError) — we never ship a still.
    missing = []
    if not topic:
        missing.append("Topic")
    if not caption:
        missing.append("Post Caption")
    if not image_url:
        missing.append("Media Image URL")
    if not video_url:
        missing.append("Media Video URL")
    if missing:
        raise RuntimeError(
            f"Row {row_index} missing required fields: {', '.join(missing)}"
        )

    slug = _slugify(topic)
    log.info("Row %d -> slug=%s", row_index, slug)

    TMP_DIR.mkdir(parents=True, exist_ok=True)
    RENDERS_DIR.mkdir(parents=True, exist_ok=True)

    # Download the source video. If it can't be fetched (download blocked,
    # empty result, error), the post is SKIPPED — no still fallback.
    log.info("Downloading source video: %s", video_url)
    try:
        source_video = fetch_single_clip(video_url, slug, max_seconds=60.0)
    except Exception as exc:  # noqa: BLE001 — map to skip
        raise NoVideoError(
            f"Row {row_index}: source video download failed ({exc})"
        ) from exc
    if not source_video or not Path(source_video).exists():
        raise NoVideoError(
            f"Row {row_index}: source video produced no file ({video_url})"
        )

    log.info("Downloading poster image: %s", image_url)
    poster_rels = fetch_image(image_url, slug, count=1)
    poster_path = REPO_ROOT / "assets" / "images" / "auto" / f"{slug}_auto.jpg"
    if not poster_path.exists() and poster_rels:
        # fetch_image returns paths relative to reels/index.html; resolve.
        poster_path = (REPO_ROOT / "reels" / poster_rels[0]).resolve()
    if not poster_path.exists():
        raise RuntimeError(f"Poster image download failed for row {row_index}")

    card_png = TMP_DIR / f"{slug}_card.png"
    log.info("Rendering tweet card -> %s", card_png.name)
    render_card(
        caption,
        handle="@genzcapital",
        display_name="Gen Z Capital",
        avatar_path=LOGO_PATH,
        out_path=card_png,
    )

    out_mp4 = RENDERS_DIR / f"{slug}-tweet.mp4"
    log.info("Compositing reel (video) -> %s", out_mp4.name)
    composite_reel(
        card_png, source_video, poster_path, out_mp4,
        preview_seconds=1.0,
        max_seconds=60.0,
    )

    if not out_mp4.exists() or out_mp4.stat().st_size == 0:
        raise RuntimeError(f"Composite produced empty file: {out_mp4}")

    log.info("Reel built: %s (%.1f MB)", out_mp4, out_mp4.stat().st_size / 1e6)
    return out_mp4


def run(row_index: int | None, *, dry_run: bool) -> int:
    config = _sheets_config()
    reader = SheetsReader(config)

    if row_index is None:
        row = _find_ready_to_run_row(reader)
        if row is None:
            log.info("No row with Status='Ready to Run' (+Topic). Nothing to do.")
            return 0
        row_index = row["_row_index"]
    else:
        row = _read_row_by_index(reader, row_index)

    log.info("Processing row %d: %r", row_index, row.get("Topic", "(no topic)"))

    # Claim the row IMMEDIATELY so a re-poll (n8n fires every minute) sees
    # "Building", not "Ready to Run", and won't kick off a duplicate render.
    if not dry_run:
        _try_update(reader, row_index, "Status", CLAIM_STATUS)

    # Self-serve media: if the row has a Topic but no Media Video/Image URL,
    # find it here (keyless: yt-dlp + Pexels + brand scrape). This means a row
    # only needs Topic + "Ready to Run" — no dependency on n8n's YouTube-API
    # search, which is the part that keeps breaking ($env block, Merge config).
    if not (row.get("Media Video URL", "").strip()
            and row.get("Media Image URL", "").strip()):
        row = _ensure_media(reader, row_index, row, dry_run=dry_run)

    # Hard rule: no real video clip -> SKIP the post (don't ship a still,
    # don't mark it Failed). Routes to the terminal SKIPPED_STATUS below.
    if not row.get("Media Video URL", "").strip():
        msg = (f"Row {row_index}: no video clip found for "
               f"{row.get('Topic', '')!r} — post skipped.")
        log.warning(msg)
        if not dry_run:
            _try_update(reader, row_index, "Status", SKIPPED_STATUS)
            _try_update(reader, row_index, "Media Status", msg[:200])
        return 0

    try:
        mp4_path = build_reel_for_row(row)
    except NoVideoError as exc:
        # No real footage -> SKIP the post (terminal, not a failure/retry).
        log.warning("Skipping row %d — no usable video: %s", row_index, exc)
        if not dry_run:
            _try_update(reader, row_index, "Status", SKIPPED_STATUS)
            _try_update(reader, row_index, "Media Status", str(exc)[:200])
        return 0
    except Exception as exc:
        log.error("Build failed: %s", exc)
        log.debug(traceback.format_exc())
        _mark_failed(reader, row_index, str(exc))
        return 1

    if dry_run:
        log.info("DRY RUN: skipping Drive upload + Sheet update.")
        log.info("Local file: %s", mp4_path)
        return 0

    # Late import so dry runs don't require googleapiclient / OAuth setup.
    from publisher.publish_reel import upload_to_drive  # noqa: E402

    try:
        download_url = upload_to_drive(mp4_path)
    except Exception as exc:
        log.error("Drive upload failed: %s", exc)
        log.debug(traceback.format_exc())
        _mark_failed(reader, row_index, f"Drive upload: {exc}")
        return 2

    _try_update(reader, row_index, "Reel MP4 URL", download_url)
    _try_update(
        reader, row_index, "Media Found At",
        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    _try_update(reader, row_index, "Status", DONE_STATUS)

    log.info("Done. Row %d -> Status=%s, mp4=%s", row_index, DONE_STATUS, download_url)
    return 0


# ---------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--row", type=int, help="1-indexed sheet row to render")
    g.add_argument("--next", action="store_true",
                   help="Render the next Status='Ready to Run' row")
    p.add_argument("--dry-run", action="store_true",
                   help="Build mp4 locally, skip Drive upload + Sheet update")
    args = p.parse_args(argv)

    return run(args.row if not args.next else None, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
