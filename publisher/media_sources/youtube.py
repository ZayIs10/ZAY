"""YouTube search via yt-dlp (no API key needed).

We use yt-dlp's `ytsearchN:<query>` extractor in extract-only mode — it
scrapes YouTube's search results page and returns metadata for the top
N videos. No download, no ffmpeg involvement.

Limitations:
- yt-dlp's `ytsearch` extractor returns sparse metadata in flat mode
  (no view count). We accept that — view count is a tiebreak, not a
  must-have. Set flat_mode=False to get full metadata at the cost of
  one HTTP request per result.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("media_sources.youtube")


def _import_ytdlp():
    """Defer import so the module loads on systems without yt-dlp."""
    try:
        from yt_dlp import YoutubeDL  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "yt-dlp is not installed. Add `yt-dlp>=2025.1.0` to "
            "requirements.txt and `pip install -r requirements.txt`."
        ) from exc
    return YoutubeDL


def _best_thumbnail(entry: dict[str, Any]) -> str:
    thumbs = entry.get("thumbnails") or []
    if thumbs:
        # Sort by area descending; fall back to last entry order if no size.
        thumbs = sorted(
            thumbs,
            key=lambda t: (t.get("width") or 0) * (t.get("height") or 0),
            reverse=True,
        )
        return thumbs[0].get("url") or ""
    return entry.get("thumbnail") or ""


def search_videos(
    query: str,
    limit: int = 5,
    *,
    channel_handle: str | None = None,
    flat_mode: bool = True,
) -> list[dict]:
    """Search YouTube for `query` and return up to `limit` Candidate dicts.

    If `channel_handle` is given (e.g. "@OpenAI"), the query is rewritten
    to constrain results to that channel.
    """
    if channel_handle:
        # yt-dlp doesn't expose a direct channel-search filter; the most
        # reliable trick is to append the handle to the query — YouTube's
        # ranking will pull that channel's videos to the top.
        query = f"{query} {channel_handle}"

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": "in_playlist" if flat_mode else False,
        "noplaylist": True,
        "default_search": "ytsearch",
    }
    YoutubeDL = _import_ytdlp()

    search_url = f"ytsearch{limit}:{query}"
    with YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(search_url, download=False)
        except Exception as exc:
            log.warning("yt-dlp search failed for %r: %s", query, exc)
            return []

    entries = (info or {}).get("entries") or []
    candidates: list[dict] = []
    for entry in entries:
        if not entry:
            continue
        video_id = entry.get("id") or entry.get("video_id")
        if not video_id:
            continue
        watch_url = entry.get("webpage_url") or (
            f"https://www.youtube.com/watch?v={video_id}"
        )
        dur = entry.get("duration") or 0
        try:
            dur = float(dur)
        except (TypeError, ValueError):
            dur = 0.0

        candidates.append({
            "source": "youtube",
            "kind": "video",
            "title": entry.get("title") or watch_url,
            "page_url": watch_url,
            # For YouTube we surface the watch URL as `media_url` too —
            # downstream renderer must use yt-dlp to fetch the actual MP4.
            "media_url": watch_url,
            "thumbnail": _best_thumbnail(entry),
            "duration_s": dur,
            "width": int(entry.get("width") or 0),
            "height": int(entry.get("height") or 0),
            "extra": {
                "video_id": video_id,
                "channel": entry.get("channel") or entry.get("uploader"),
                "channel_id": entry.get("channel_id"),
                "view_count": entry.get("view_count"),
                "is_official": bool(channel_handle),
            },
        })
    return candidates


def search_images(query: str, limit: int = 5) -> list[dict]:
    """YouTube has no image-only search — return empty so the scoring
    module can skip cleanly. The video thumbnails are already used by
    the renderer if needed."""
    return []
