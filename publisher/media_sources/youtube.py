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
import os
from pathlib import Path
from typing import Any

import requests

log = logging.getLogger("media_sources.youtube")

_REPO_ROOT = Path(__file__).resolve().parents[2]

_YT_API_SEARCH = "https://www.googleapis.com/youtube/v3/search"


def _youtube_api_key() -> str | None:
    key = (os.getenv("YOUTUBE_API_KEY") or "").strip()
    if not key:
        # Defensive: this module may be imported before the entry script
        # loads .env. Load it once here so the API path isn't silently
        # skipped (which would drop us to the bot-blocked scrape in CI).
        try:
            from dotenv import load_dotenv
            load_dotenv(_REPO_ROOT / ".env")
            key = (os.getenv("YOUTUBE_API_KEY") or "").strip()
        except Exception:  # noqa: BLE001 — dotenv optional
            pass
    return key or None


def _search_via_api(
    query: str,
    limit: int,
    *,
    channel_id: str | None = None,
) -> list[dict]:
    """Search YouTube via the Data API v3. Reliable from datacenter IPs
    (unlike the yt-dlp scrape, which YouTube bot-blocks from CI). Returns
    Candidate dicts; the watch URL is still downloaded by yt-dlp later.

    Raises on any API/HTTP error so the caller can fall back to scraping.
    """
    key = _youtube_api_key()
    if not key:
        raise RuntimeError("no YOUTUBE_API_KEY")

    params = {
        "part": "snippet",
        "type": "video",
        "order": "relevance",
        "maxResults": str(min(max(limit, 1), 10)),
        "q": query,
        "key": key,
    }
    # Bias toward recent uploads for news topics: the API lets us hard-filter
    # to videos published after a date. We don't filter (relevance still leads),
    # but we DO request publishedAt below so scoring can reward fresh clips —
    # the fix for "just released" topics pulling months-old footage.
    if channel_id:
        params["channelId"] = channel_id

    r = requests.get(_YT_API_SEARCH, params=params, timeout=20)
    if r.status_code != 200:
        # Surface quota/key errors clearly, then let the caller fall back.
        snippet = (r.text or "")[:160]
        raise RuntimeError(f"YouTube API HTTP {r.status_code}: {snippet}")
    data = r.json()

    candidates: list[dict] = []
    for it in data.get("items", []):
        vid = (it.get("id") or {}).get("videoId")
        if not vid:
            continue
        sn = it.get("snippet") or {}
        thumbs = sn.get("thumbnails") or {}
        thumb = (
            (thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {})
            .get("url", "")
        )
        watch_url = f"https://www.youtube.com/watch?v={vid}"
        candidates.append({
            "source": "youtube",
            "kind": "video",
            "title": sn.get("title") or watch_url,
            "page_url": watch_url,
            "media_url": watch_url,
            "thumbnail": thumb,
            # The search endpoint doesn't return duration; scoring treats
            # 0 as "unknown" (no duration bonus/penalty), which is fine.
            "duration_s": 0.0,
            "width": 0,
            "height": 0,
            "extra": {
                "video_id": vid,
                "channel": sn.get("channelTitle"),
                "channel_id": sn.get("channelId"),
                # publishedAt is an ISO-8601 string (e.g. 2026-06-20T13:00:00Z);
                # scoring._recency_signal turns it into a freshness boost.
                "published_at": sn.get("publishedAt"),
                "view_count": None,
                "is_official": bool(channel_id),
                "via": "api",
            },
        })
    return candidates


def _youtube_cookiefile() -> str | None:
    """Locate a Netscape-format YouTube cookies file, if available.

    YouTube bot-blocks datacenter IPs (e.g. GitHub Actions) with "Sign in
    to confirm you're not a bot" — and that block hits the SEARCH scrape too,
    not just downloads, so `ytsearch` returns 0 results in CI without cookies.
    Mirrors media_consumer._youtube_cookiefile (kept local to avoid importing
    the heavier media_consumer module from a lightweight search source).
    """
    env_path = os.getenv("YOUTUBE_COOKIES_FILE", "").strip()
    candidates = [env_path] if env_path else []
    candidates.append(str(_REPO_ROOT / "youtube_cookies.txt"))
    for c in candidates:
        if c and Path(c).exists() and Path(c).stat().st_size > 0:
            return c
    return None


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
    channel_id: str | None = None,
    flat_mode: bool = True,
) -> list[dict]:
    """Search YouTube for `query` and return up to `limit` Candidate dicts.

    Strategy:
      1. If YOUTUBE_API_KEY is set, use the Data API — it's RELIABLE from
         datacenter IPs (GitHub Actions), where the yt-dlp search scrape is
         bot-blocked and returns 0 results. The API gives clean video IDs +
         titles (titles feed the demo-vs-talking-head scorer). channel_id
         constrains to an official channel exactly.
      2. Otherwise (or if the API errors / hits quota) fall back to the
         yt-dlp ytsearch scrape, appending the handle to bias results.

    Either way the result is a watch URL; yt-dlp + cookies downloads the
    actual MP4 later (the download path is NOT bot-blocked with cookies).
    """
    # --- Preferred: YouTube Data API (works from the cloud) ---------------
    if _youtube_api_key():
        try:
            cands = _search_via_api(query, limit, channel_id=channel_id)
            log.info("YouTube API search %r -> %d results", query, len(cands))
            return cands
        except Exception as exc:
            log.warning(
                "YouTube API search failed (%s) — falling back to scrape.", exc
            )

    # --- Fallback: yt-dlp scrape ------------------------------------------
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

    # Same anti-bot-block treatment the download path uses: authenticated
    # cookies + the EJS challenge solver. Without cookies, YouTube returns
    # 0 search results from datacenter IPs (GitHub Actions), which silently
    # left rows with no video and failed the build.
    cookiefile = _youtube_cookiefile()
    if cookiefile:
        ydl_opts["cookiefile"] = cookiefile
    else:
        log.warning(
            "No YouTube cookies file found — search may return 0 results from "
            "datacenter IPs (e.g. CI). Set the YOUTUBE_COOKIES secret to fix."
        )
    ydl_opts["remote_components"] = ["ejs:github"]

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
                # yt-dlp gives upload_date as YYYYMMDD (flat search often omits
                # it; full metadata has it). timestamp is a unix epoch. Either
                # feeds scoring._recency_signal; absent -> no recency effect.
                "upload_date": entry.get("upload_date"),
                "timestamp": entry.get("timestamp"),
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
