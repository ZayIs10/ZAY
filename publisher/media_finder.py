"""publisher/media_finder.py — auto-pick background media for reel topics.

For each pending row on the "Reels" tab of the Gen Z Capital Google
Sheet, this script:
  1. Detects whether the Topic mentions a known AI brand
     (publisher/media_sources/brand_detect.py).
  2. Runs searches in parallel:
       - brand's official YouTube channel + blog (if matched)
       - YouTube general search (yt-dlp, keyless)
       - DuckDuckGo image search (preview image fallback)
  3. Scores all candidates (publisher/media_sources/scoring.py),
     picks the top video URL + top image URL.
  4. Writes the URLs back into the same Reels row.

Trigger: invoked from .github/workflows/find_topic_media.yml, which
n8n fires via repository_dispatch once it appends a new draft row.

CLI:
    python publisher/media_finder.py --all-pending
    python publisher/media_finder.py --row 17
    python publisher/media_finder.py --row 17 --force
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import gspread
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from publisher.media_sources import (
    brand_detect,
    brand_official,
    google_images,
    pexels,
    youtube,
)
from publisher.media_sources.scoring import pick_best

SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]

MEDIA_COLUMNS = [
    "Media Video URL",
    "Media Image URL",
    "Media Source",
    "Media Backups (JSON)",
    "Media Status",
    "Media Found At",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("media_finder")


# ---------------------------------------------------------------------------
# Sheet helpers
# ---------------------------------------------------------------------------

def _open_worksheet():
    load_dotenv(REPO_ROOT / ".env")
    sheet_id = os.getenv("GOOGLE_SHEET_ID")
    if not sheet_id:
        raise RuntimeError("GOOGLE_SHEET_ID is not set in .env")
    sheet_name = os.getenv("GOOGLE_SHEET_REELS_NAME", "Reels")

    creds_path = REPO_ROOT / "google_service_account.json"
    if not creds_path.exists():
        raise RuntimeError(f"Missing {creds_path}")

    creds = Credentials.from_service_account_file(
        str(creds_path), scopes=SCOPES,
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(sheet_id)
    return sh.worksheet(sheet_name)


def _col_index(headers: list[str], name: str) -> int:
    try:
        return headers.index(name) + 1  # gspread is 1-indexed
    except ValueError:
        raise RuntimeError(
            f"Column {name!r} missing from sheet. "
            f"Run scripts/setup_reels_sheet.py to add it."
        )


def _read_row(ws, row_index: int) -> tuple[list[str], dict[str, str]]:
    headers = ws.row_values(1)
    raw = ws.row_values(row_index)
    row = {h: (raw[i] if i < len(raw) else "") for i, h in enumerate(headers)}
    return headers, row


def _select_pending_rows(ws) -> list[int]:
    """Return 1-indexed row numbers that have Topic set but Media Status
    is blank or 'pending'."""
    all_values = ws.get_all_values()
    if not all_values:
        return []
    headers = all_values[0]
    try:
        topic_col = headers.index("Topic")
        status_col = headers.index("Media Status")
    except ValueError as exc:
        raise RuntimeError(
            f"Sheet missing required column: {exc}. "
            f"Run scripts/setup_reels_sheet.py first."
        )

    pending: list[int] = []
    for i, raw in enumerate(all_values[1:], start=2):
        topic = raw[topic_col] if topic_col < len(raw) else ""
        status = (raw[status_col] if status_col < len(raw) else "").strip().lower()
        if topic.strip() and status in ("", "pending"):
            pending.append(i)
    return pending


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def _build_query(topic: str, key_points: str) -> str:
    """Compose the search query. Topic is primary; the first ~80 chars of
    Key Points add specificity."""
    q = topic.strip()
    if key_points and len(q) < 80:
        snippet = key_points.strip().split(".")[0][:80]
        q = f"{q} {snippet}".strip()
    return q


def discover_for_topic(topic: str, key_points: str) -> dict:
    """Run all sources in parallel and rank candidates.

    Returns:
        {
          "matched_brands": [...],
          "video":   {"winner": <candidate>|None, "backups": [...]},
          "image":   {"winner": <candidate>|None, "backups": [...]},
        }
    """
    detect_text = f"{topic} {key_points}".strip()
    matched = brand_detect.detect_brands(detect_text)
    query = _build_query(topic, key_points)
    log.info("Topic %r -> brands=%s, query=%r", topic, matched, query)

    # Each entry: (label, callable returning list[Candidate])
    tasks: list[tuple[str, callable]] = []

    for brand in matched:
        tasks.append((
            f"{brand}_official_video",
            lambda b=brand: brand_official.search_videos(b, query, limit=5),
        ))
        tasks.append((
            f"{brand}_official_image",
            lambda b=brand: brand_official.search_images(b, query, limit=5),
        ))

    tasks.extend([
        ("youtube_video", lambda: youtube.search_videos(query, limit=5)),
        ("ddg_image", lambda: google_images.search_images(query, limit=8)),
        # Pexels = copyright-safe, directly-downloadable mp4 fallback so a
        # row never ends up with only un-downloadable URLs.
        ("pexels_video", lambda: pexels.search_videos(query, limit=5)),
        ("pexels_image", lambda: pexels.search_images(query, limit=5)),
    ])

    all_candidates: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        future_to_label = {pool.submit(fn): label for label, fn in tasks}
        for fut in concurrent.futures.as_completed(future_to_label):
            label = future_to_label[fut]
            try:
                results = fut.result(timeout=60) or []
            except Exception as exc:
                log.warning("source %s raised: %s", label, exc)
                results = []
            log.info("  %-25s -> %d candidates", label, len(results))
            all_candidates.extend(results)

    videos = [c for c in all_candidates if c.get("kind") == "video"]
    images = [c for c in all_candidates if c.get("kind") == "image"]

    v_winner, v_backups = pick_best(videos, matched_brands=matched)
    i_winner, i_backups = pick_best(images, matched_brands=matched)

    return {
        "matched_brands": matched,
        "video": {"winner": v_winner, "backups": v_backups},
        "image": {"winner": i_winner, "backups": i_backups},
    }


# ---------------------------------------------------------------------------
# Sheet write
# ---------------------------------------------------------------------------

def _backups_json(video_backups: list[dict], image_backups: list[dict]) -> str:
    """Serialize backup candidates to a compact JSON list."""
    def trim(c: dict) -> dict:
        return {
            "source": c.get("source"),
            "kind": c.get("kind"),
            "title": c.get("title"),
            "page_url": c.get("page_url"),
            "media_url": c.get("media_url"),
            "thumbnail": c.get("thumbnail"),
        }
    return json.dumps(
        {
            "video": [trim(c) for c in video_backups],
            "image": [trim(c) for c in image_backups],
        },
        ensure_ascii=False,
    )


def write_row_media(ws, row_index: int, result: dict) -> str:
    """Write discovery output back to the row. Returns the final status
    string written ('found' | 'partial' | 'failed')."""
    headers = ws.row_values(1)
    v = result["video"]["winner"]
    i = result["image"]["winner"]

    video_url = (v or {}).get("media_url", "")
    image_url = (i or {}).get("media_url", "")
    sources: list[str] = []
    if v:
        sources.append(v.get("source", ""))
    if i:
        sources.append(i.get("source", ""))
    source_label = " | ".join(s for s in sources if s) or ""

    if v and i:
        status = "found"
    elif v or i:
        status = "partial"
    else:
        status = "failed"

    backups = _backups_json(
        result["video"]["backups"], result["image"]["backups"],
    )
    found_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    updates = [
        ("Media Video URL",      video_url),
        ("Media Image URL",      image_url),
        ("Media Source",         source_label),
        ("Media Backups (JSON)", backups),
        ("Media Status",         status),
        ("Media Found At",       found_at),
    ]
    for col_name, value in updates:
        col_idx = _col_index(headers, col_name)
        ws.update_cell(row_index, col_idx, value)
    return status


# ---------------------------------------------------------------------------
# Per-row driver
# ---------------------------------------------------------------------------

def process_row(ws, row_index: int, *, force: bool = False) -> str:
    headers, row = _read_row(ws, row_index)
    topic = (row.get("Topic") or "").strip()
    if not topic:
        log.warning("row %d has no Topic, skipping", row_index)
        return "skipped"

    existing_status = (row.get("Media Status") or "").strip().lower()
    if existing_status == "found" and not force:
        log.info("row %d already has Media Status=found; pass --force to re-run",
                 row_index)
        return "skipped"

    key_points = row.get("Key Points") or ""
    result = discover_for_topic(topic, key_points)
    status = write_row_media(ws, row_index, result)
    log.info("row %d -> %s (video=%s, image=%s)",
             row_index, status,
             bool(result["video"]["winner"]),
             bool(result["image"]["winner"]))
    return status


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--row", type=int,
        help="Process a single row (1-indexed; matches the Sheet's row number).",
    )
    group.add_argument(
        "--all-pending", action="store_true",
        help="Process every Reels row where Media Status is blank or 'pending'.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-process rows even if Media Status is already 'found'.",
    )
    args = parser.parse_args()

    ws = _open_worksheet()

    if args.row is not None:
        process_row(ws, args.row, force=args.force)
        return 0

    pending = _select_pending_rows(ws)
    if not pending:
        log.info("No pending rows found.")
        return 0

    log.info("Processing %d pending row(s): %s", len(pending), pending)
    for row_idx in pending:
        try:
            process_row(ws, row_idx, force=args.force)
        except Exception as exc:
            log.exception("row %d failed: %s", row_idx, exc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
