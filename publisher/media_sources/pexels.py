"""Pexels URL-only wrapper.

Reuses the existing search functions in `scripts/pexels_fetcher.py`
without downloading anything. Returns Candidate dicts the same shape
as the other media_sources modules.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Reuse existing Pexels client — same auth, same rate-limit budget.
from scripts.pexels_fetcher import search_pexels, search_videos as _pexels_search_videos, _pick_video_file  # noqa: E402

log = logging.getLogger("media_sources.pexels")


def search_videos(query: str, limit: int = 5) -> list[dict]:
    """Search Pexels videos and return Candidate dicts (no download)."""
    try:
        videos = _pexels_search_videos(query, count=limit)
    except Exception as exc:
        log.warning("Pexels video search failed for %r: %s", query, exc)
        return []

    candidates: list[dict] = []
    for video in videos:
        media_url = _pick_video_file(video)
        if not media_url:
            continue
        image_thumb = video.get("image") or ""
        dur = video.get("duration") or 0
        try:
            dur = float(dur)
        except (TypeError, ValueError):
            dur = 0.0

        # Pexels portrait video size: use the picked file's metadata
        width = 0
        height = 0
        for f in video.get("video_files", []):
            if f.get("link") == media_url:
                width = int(f.get("width") or 0)
                height = int(f.get("height") or 0)
                break

        candidates.append({
            "source": "pexels_video",
            "kind": "video",
            "title": (video.get("user") or {}).get("name") or "Pexels video",
            "page_url": video.get("url") or media_url,
            "media_url": media_url,
            "thumbnail": image_thumb,
            "duration_s": dur,
            "width": width,
            "height": height,
            "extra": {"pexels_id": video.get("id")},
        })
    return candidates


def search_images(query: str, limit: int = 5) -> list[dict]:
    """Search Pexels photos and return Candidate dicts (no download)."""
    try:
        photos = search_pexels(query, count=limit)
    except Exception as exc:
        log.warning("Pexels photo search failed for %r: %s", query, exc)
        return []

    candidates: list[dict] = []
    for photo in photos:
        src = photo.get("src") or {}
        media_url = src.get("large2x") or src.get("original") or src.get("large")
        if not media_url:
            continue
        candidates.append({
            "source": "pexels_photo",
            "kind": "image",
            "title": photo.get("photographer") or "Pexels photo",
            "page_url": photo.get("url") or media_url,
            "media_url": media_url,
            "thumbnail": src.get("medium") or src.get("small") or media_url,
            "duration_s": 0.0,
            "width": int(photo.get("width") or 0),
            "height": int(photo.get("height") or 0),
            "extra": {"pexels_id": photo.get("id")},
        })
    return candidates
