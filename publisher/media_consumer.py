"""Consume the URLs that media_finder.py wrote to the Reels row and
turn them into the per-beat asset files reel_generator.py expects.

Inputs (from the sheet row):
    row["Media Video URL"]  — direct mp4 OR YouTube watch URL
    row["Media Image URL"]  — direct image URL

Outputs (paths relative to `reels/index.html`, just like pexels_fetcher):
    fetch_video(url, slug, durations) -> [".../<slug>_b1.mp4", ...]
    fetch_image(url, slug, count)     -> [".../<slug>_1.jpg",  ...]

YouTube watch URLs need yt-dlp; everything else is a plain HTTP fetch.
Per-beat cutting reuses the same ffmpeg invocation pattern as
scripts/pexels_fetcher.py so the renderer sees identical-shape inputs.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
CLIP_DIR = REPO_ROOT / "reels" / "assets" / "clips"
IMG_DIR = REPO_ROOT / "assets" / "images" / "auto"

log = logging.getLogger("media_consumer")


def _youtube_cookiefile() -> str | None:
    """Locate a Netscape-format YouTube cookies file, if one is available.

    YouTube bot-blocks datacenter IPs (e.g. GitHub Actions) with
    "Sign in to confirm you're not a bot". Passing authenticated cookies
    bypasses that. The CI workflow writes the YOUTUBE_COOKIES secret to a
    file and points YOUTUBE_COOKIES_FILE at it; locally the default path
    is used if it exists.
    """
    env_path = os.getenv("YOUTUBE_COOKIES_FILE", "").strip()
    candidates = [env_path] if env_path else []
    candidates.append(str(REPO_ROOT / "youtube_cookies.txt"))
    for c in candidates:
        if c and Path(c).exists() and Path(c).stat().st_size > 0:
            return c
    return None


def _resolve_ffmpeg() -> str:
    bundled = REPO_ROOT / "node_modules" / "@ffmpeg-installer" / "win32-x64" / "ffmpeg.exe"
    if bundled.exists():
        return str(bundled)
    capcut_root = Path.home() / "AppData" / "Local" / "CapCut" / "Apps"
    if capcut_root.exists():
        for ff in capcut_root.glob("*/ffmpeg.exe"):
            return str(ff)
    return "ffmpeg"


def _resolve_ffprobe() -> str:
    ff = _resolve_ffmpeg()
    if ff.lower().endswith("ffmpeg.exe"):
        probe = Path(ff).with_name("ffprobe.exe")
        if probe.exists():
            return str(probe)
    return "ffprobe"


def _is_youtube(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return "youtube.com" in host or "youtu.be" in host


def _http_download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = requests.get(url, timeout=60, stream=True)
    r.raise_for_status()
    with dest.open("wb") as f:
        for chunk in r.iter_content(chunk_size=64 * 1024):
            if chunk:
                f.write(chunk)


def _ytdlp_download(url: str, dest: Path) -> None:
    """Download best portrait-leaning MP4 via yt-dlp."""
    try:
        from yt_dlp import YoutubeDL  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "yt-dlp is required to download YouTube URLs. "
            "Run: pip install -r requirements.txt"
        ) from exc

    dest.parent.mkdir(parents=True, exist_ok=True)
    ydl_opts = {
        # Permissive: any video+audio, merged to mp4. The strict ext=mp4
        # filter could leave "Requested format is not available" when the
        # chosen player client only exposes webm/av1 streams.
        "format": "bv*+ba/b",
        "outtmpl": str(dest),
        "quiet": True,
        "no_warnings": True,
        "merge_output_format": "mp4",
        "retries": 5,
        "fragment_retries": 5,
        # YouTube now gates real format URLs behind a JS "n challenge".
        # yt-dlp solves it with a JS runtime (Deno) plus the EJS solver
        # scripts fetched from GitHub. Without this, only storyboard images
        # are offered -> "Requested format is not available".
        "remote_components": ["ejs:github"],
    }

    ff = _resolve_ffmpeg()
    if ff and ff != "ffmpeg":
        # yt-dlp merges video+audio with ffmpeg; point it at the resolved
        # binary so local runs (no ffmpeg on PATH) work like CI does.
        ydl_opts["ffmpeg_location"] = ff

    cookiefile = _youtube_cookiefile()
    if cookiefile:
        log.info("Using YouTube cookies: %s", cookiefile)
        ydl_opts["cookiefile"] = cookiefile
    else:
        log.warning(
            "No YouTube cookies file found — datacenter IPs (e.g. CI) may be "
            "bot-blocked. Set the YOUTUBE_COOKIES secret to fix this."
        )

    with YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])


def _ffmpeg_cut_to_beat(src: Path, dest: Path, duration_s: float) -> None:
    """Re-encode a section of src to a 1080x1920 30fps mp4, audio dropped.
    Mirrors the ffmpeg invocation in scripts/pexels_fetcher.py so the
    HyperFrames renderer sees consistent keyframe spacing."""
    target = max(duration_s + 0.5, 1.0)
    cmd = [
        _resolve_ffmpeg(), "-y", "-loglevel", "warning",
        "-i", str(src),
        "-t", f"{target:.2f}",
        "-vf",
        "scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-r", "30", "-g", "30", "-keyint_min", "30",
        "-movflags", "+faststart",
        "-an",
        str(dest),
    ]
    proc = subprocess.run(cmd)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed to cut {src} -> {dest} (exit {proc.returncode})"
        )


def fetch_video(url: str, slug: str, durations: list[float]) -> list[str]:
    """Download `url` once, then cut into one clip per beat. Returns paths
    relative to `reels/index.html`."""
    CLIP_DIR.mkdir(parents=True, exist_ok=True)
    raw = CLIP_DIR / f"_auto_raw_{slug}.mp4"

    if _is_youtube(url):
        log.info("Downloading YouTube clip via yt-dlp: %s", url)
        _ytdlp_download(url, raw)
    else:
        log.info("Downloading direct video: %s", url)
        _http_download(url, raw)

    rels: list[str] = []
    for i, dur in enumerate(durations, start=1):
        cut = CLIP_DIR / f"{slug}_b{i}.mp4"
        _ffmpeg_cut_to_beat(raw, cut, dur)
        rels.append(f"assets/clips/{cut.name}")
        log.info("  beat %d -> %s (%.1fs)", i, cut.name, dur)

    return rels


def fetch_single_clip(url: str, slug: str, *, max_seconds: float = 60.0) -> Path:
    """Download `url` once, return ONE mp4 path. Used by the tweet-card
    reel pipeline (the static caption + variable-length source video
    format) where downstream wants the whole clip, not per-beat splits.

    The clip is NOT re-encoded here — duration capping and scale/crop
    happen later in `publisher/compositor.py` so the source bytes stay
    on disk for retry / debugging without burning a transcode pass.
    """
    CLIP_DIR.mkdir(parents=True, exist_ok=True)
    raw = CLIP_DIR / f"_single_{slug}.mp4"

    if _is_youtube(url):
        log.info("Downloading YouTube clip via yt-dlp: %s", url)
        _ytdlp_download(url, raw)
    else:
        log.info("Downloading direct video: %s", url)
        _http_download(url, raw)

    if not raw.exists() or raw.stat().st_size == 0:
        raise RuntimeError(f"Download produced empty file: {raw}")

    log.info("Source clip saved -> %s (%.1f MB)", raw.name, raw.stat().st_size / 1e6)
    _ = max_seconds  # consumer caps duration; argument kept for caller clarity
    return raw


def fetch_image(url: str, slug: str, count: int = 4) -> list[str]:
    """Download `url` once, then duplicate the file `count` times. The
    renderer's image-overlay logic expects N distinct paths; we give it
    N copies of the same image (cheap, no per-beat re-encoding needed)."""
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    base = IMG_DIR / f"{slug}_auto.jpg"
    log.info("Downloading auto image: %s", url)
    _http_download(url, base)

    rels: list[str] = []
    for i in range(1, count + 1):
        dest = IMG_DIR / f"{slug}_auto_{i}.jpg"
        if dest != base:
            shutil.copyfile(base, dest)
        rels.append(f"../assets/images/auto/{dest.name}")
    return rels
