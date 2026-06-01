"""
Pexels stock photo fetcher — copyright-safe cinematic backgrounds for reels.

Pexels (https://www.pexels.com/license/) is free for commercial use, no
attribution required, and the API tier is generous (200 req/hr, 20K/month).
This is the right source for reel B-roll while the GenZ Capital account is
still building authority — it avoids the copyright/ban risk of scraping
other Instagram or YouTube creators.

Output: portrait JPGs saved to `assets/images/pexels/<slug>_<n>.jpg`, with
the relative-to-`reels/index.html` path returned (`../assets/images/pexels/...`),
so the result is a drop-in replacement for `pick_images()` in
`publisher/reel_generator.py`.

Usage:
    # CLI smoke test — downloads 4 portrait photos for a query
    python scripts/pexels_fetcher.py "AI automation startup" --count 4 --slug demo

    # From Python
    from scripts.pexels_fetcher import search_and_download
    paths = search_and_download("AI automation startup", count=4, slug="demo")

Required env var: PEXELS_API_KEY (sign up free at https://www.pexels.com/api/).
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
PEXELS_DIR = REPO_ROOT / "assets" / "images" / "pexels"
PEXELS_VIDEO_DIR = REPO_ROOT / "reels" / "assets" / "clips"

PEXELS_SEARCH_URL = "https://api.pexels.com/v1/search"
PEXELS_VIDEO_SEARCH_URL = "https://api.pexels.com/videos/search"


def _resolve_ffmpeg() -> str:
    candidate = REPO_ROOT / "node_modules" / "@ffmpeg-installer" / "win32-x64" / "ffmpeg.exe"
    return str(candidate) if candidate.exists() else "ffmpeg"

log = logging.getLogger("pexels_fetcher")


def _api_key() -> str:
    load_dotenv(REPO_ROOT / ".env")
    key = os.getenv("PEXELS_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "PEXELS_API_KEY is not set. Sign up free at https://www.pexels.com/api/ "
            "and add `PEXELS_API_KEY=...` to .env"
        )
    return key


def search_pexels(query: str, count: int = 4, *, page: int = 1) -> list[dict]:
    """Hit Pexels search API and return up to `count` photo dicts.

    `orientation=portrait` is set so the photos crop nicely to 1080×1920.
    """
    headers = {"Authorization": _api_key()}
    params = {
        "query": query,
        "per_page": max(count * 3, 15),  # over-fetch so we can skip duds
        "orientation": "portrait",
        "page": page,
    }
    resp = requests.get(PEXELS_SEARCH_URL, headers=headers, params=params, timeout=20)
    resp.raise_for_status()
    photos = resp.json().get("photos", [])
    if not photos:
        raise RuntimeError(f"Pexels returned 0 results for query {query!r}")
    return photos[:count]


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = requests.get(url, timeout=30, stream=True)
    r.raise_for_status()
    with dest.open("wb") as f:
        for chunk in r.iter_content(chunk_size=64 * 1024):
            if chunk:
                f.write(chunk)


def search_and_download(query: str, count: int = 4, *, slug: str = "reel") -> list[str]:
    """Search Pexels for `query`, download `count` portrait photos, return paths
    relative to `reels/index.html` (i.e. prefixed with `../`)."""
    photos = search_pexels(query, count=count)
    rels: list[str] = []
    for i, photo in enumerate(photos, start=1):
        url = photo["src"].get("large2x") or photo["src"].get("original")
        if not url:
            continue
        out = PEXELS_DIR / f"{slug}_{i}.jpg"
        _download(url, out)
        rels.append(f"../assets/images/pexels/{out.name}")
        log.info("Downloaded %s (Pexels photo id=%s) -> %s", url, photo.get("id"), out)
    if len(rels) < count:
        raise RuntimeError(
            f"Only {len(rels)}/{count} Pexels images downloaded for {query!r}"
        )
    return rels


def search_videos(query: str, count: int = 5, *, page: int = 1) -> list[dict]:
    """Search Pexels videos. orientation=portrait so they crop to 1080x1920."""
    headers = {"Authorization": _api_key()}
    params = {
        "query": query,
        "per_page": max(count * 3, 15),
        "orientation": "portrait",
        "size": "medium",
        "page": page,
    }
    resp = requests.get(PEXELS_VIDEO_SEARCH_URL, headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    videos = resp.json().get("videos", [])
    if not videos:
        raise RuntimeError(f"Pexels videos returned 0 results for query {query!r}")
    return videos[:count]


def _pick_video_file(video: dict) -> str | None:
    """Pick the highest-res portrait MP4 file under ~1080w from the video entry."""
    files = [f for f in video.get("video_files", []) if f.get("file_type") == "video/mp4"]
    if not files:
        return None
    # Prefer portrait, height >= 1280, width <= 1280, ranked by height
    portrait = [f for f in files if (f.get("height") or 0) >= (f.get("width") or 0)]
    if portrait:
        portrait.sort(key=lambda f: abs((f.get("height") or 0) - 1920))
        return portrait[0].get("link")
    files.sort(key=lambda f: -(f.get("height") or 0))
    return files[0].get("link")


def search_and_download_videos(
    query: str,
    durations: list[float],
    *,
    slug: str = "reel",
) -> list[str]:
    """Fetch one Pexels video per beat, pre-cut to that beat's duration, scaled
    to 1080x1920. Returns paths relative to `reels/index.html`
    (i.e. `assets/clips/<slug>_b<n>.mp4`).

    Pre-cutting per beat is critical: HyperFrames chokes when multiple <video>
    elements point at the same source with #t= URL fragments (see memory:
    Reels Pipeline). One file per scene, no fragments, preload="auto".
    """
    count = len(durations)
    videos = search_videos(query, count=count)
    ffmpeg = _resolve_ffmpeg()
    PEXELS_VIDEO_DIR.mkdir(parents=True, exist_ok=True)

    rels: list[str] = []
    for i, (video, dur) in enumerate(zip(videos, durations), start=1):
        url = _pick_video_file(video)
        if not url:
            log.warning("No usable mp4 file in Pexels video id=%s", video.get("id"))
            continue

        raw = PEXELS_VIDEO_DIR / f"_raw_{slug}_b{i}.mp4"
        cut = PEXELS_VIDEO_DIR / f"{slug}_b{i}.mp4"
        log.info("Downloading Pexels video id=%s (%.1fs target) -> %s",
                 video.get("id"), dur, raw)
        _download(url, raw)

        # Pad target by 0.5s so frame-capture has slack at the tail.
        target = max(dur + 0.5, 1.0)
        # Scale + center-crop to 1080x1920, drop audio (we mux voiceover later).
        # Force a keyframe every frame (-g 30 -keyint_min 30 at 30 fps).
        # HyperFrames seeks per frame for capture; sparse keyframes cause
        # "Target closed" worker crashes mid-render.
        cmd = [
            ffmpeg, "-y", "-loglevel", "warning",
            "-i", str(raw),
            "-t", f"{target:.2f}",
            "-vf",
            "scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-r", "30", "-g", "30", "-keyint_min", "30",
            "-movflags", "+faststart",
            "-an",
            str(cut),
        ]
        proc = subprocess.run(cmd)
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg pre-cut failed for beat {i} (exit {proc.returncode})")
        try:
            raw.unlink()
        except OSError:
            pass
        rels.append(f"assets/clips/{cut.name}")
        log.info("Cut beat %d -> %s", i, cut)

    if len(rels) < count:
        raise RuntimeError(f"Only {len(rels)}/{count} Pexels clips ready for {query!r}")
    return rels


def search_and_download_videos_multi(
    queries: list[str],
    durations: list[float],
    *,
    slug: str = "reel",
    fallback_query: str = "abstract technology background",
) -> list[str]:
    """Fetch one Pexels video per beat, each with its OWN search query.

    Unlike `search_and_download_videos` (one query for the whole reel), every
    beat searches on a query tuned to what that beat is about. For niche
    subjects like AI / tech / business this returns far more relevant clips
    than a single derived query, which tends to surface generic stock filler.

    `queries` and `durations` must be the same length (one entry per beat).
    If a beat's query returns nothing, it retries once with `fallback_query`.
    Returns paths relative to `reels/index.html` (`assets/clips/<slug>_b<n>.mp4`).
    """
    if len(queries) != len(durations):
        raise ValueError("queries and durations must be the same length")

    ffmpeg = _resolve_ffmpeg()
    PEXELS_VIDEO_DIR.mkdir(parents=True, exist_ok=True)

    rels: list[str] = []
    for i, (query, dur) in enumerate(zip(queries, durations), start=1):
        cut = PEXELS_VIDEO_DIR / f"{slug}_b{i}.mp4"
        # Respect a pre-placed clip (e.g. a hand-picked YouTube/screen-recording
        # for a specific beat). Skips Pexels for that beat entirely.
        if cut.exists() and cut.stat().st_size > 100_000:
            log.info("Beat %d: pre-existing clip at %s — skipping Pexels fetch",
                     i, cut)
            rels.append(f"assets/clips/{cut.name}")
            continue

        try:
            videos = search_videos(query, count=1)
        except RuntimeError:
            log.warning("Beat %d: no Pexels video for %r — falling back to %r",
                        i, query, fallback_query)
            videos = search_videos(fallback_query, count=1)

        url = _pick_video_file(videos[0])
        if not url:
            raise RuntimeError(f"No usable mp4 in Pexels video for beat {i} ({query!r})")

        raw = PEXELS_VIDEO_DIR / f"_raw_{slug}_b{i}.mp4"
        log.info("Beat %d query=%r -> Pexels video id=%s (%.1fs target)",
                 i, query, videos[0].get("id"), dur)
        _download(url, raw)

        # Pad target by 0.5s for frame-capture slack; scale + center-crop to
        # 1080x1920; drop audio; force a keyframe every frame so HyperFrames
        # can seek per-frame without "Target closed" worker crashes.
        target = max(dur + 0.5, 1.0)
        cmd = [
            ffmpeg, "-y", "-loglevel", "warning",
            "-i", str(raw),
            "-t", f"{target:.2f}",
            "-vf",
            "scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-r", "30", "-g", "30", "-keyint_min", "30",
            "-movflags", "+faststart",
            "-an",
            str(cut),
        ]
        proc = subprocess.run(cmd)
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg pre-cut failed for beat {i} (exit {proc.returncode})")
        try:
            raw.unlink()
        except OSError:
            pass
        rels.append(f"assets/clips/{cut.name}")
        log.info("Cut beat %d -> %s", i, cut)

    return rels


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", help="Search query, e.g. 'AI automation startup'")
    parser.add_argument("--count", type=int, default=4)
    parser.add_argument("--slug", default="reel")
    parser.add_argument("--video", action="store_true",
                        help="Fetch portrait videos instead of photos and pre-cut "
                             "one per beat (default beat durations: 3,7,10,7,3).")
    parser.add_argument("--durations", default="3,7,10,7,3",
                        help="Comma-separated per-beat durations (seconds). Only used with --video.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    try:
        if args.video:
            durs = [float(x) for x in args.durations.split(",") if x.strip()]
            paths = search_and_download_videos(args.query, durs, slug=args.slug)
        else:
            paths = search_and_download(args.query, count=args.count, slug=args.slug)
    except Exception as e:
        log.error("Pexels fetch failed: %s", e)
        sys.exit(1)

    for p in paths:
        print(p)


if __name__ == "__main__":
    main()
