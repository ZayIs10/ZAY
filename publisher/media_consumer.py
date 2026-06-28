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
    """Locate a usable ffmpeg, preferring a REAL one on PATH.

    Order matters: a modern ffmpeg on PATH (e.g. the winget Gyan build on the
    self-hosted runner, or apt's ffmpeg in CI) wins FIRST. The node bundled
    binary is an ancient 2018 build that rejects modern flags like `-crf` in
    our composite command ("Unrecognized option 'crf'"), and CapCut ships a
    stripped build — both are last-resort fallbacks only used when nothing
    real is installed. (Previously bundled was tried first, which silently
    routed every render through that broken 2018 ffmpeg.)
    """
    on_path = shutil.which("ffmpeg")
    if on_path:
        return on_path
    # A full ffmpeg installed via winget (the self-hosted runner) lives under
    # WinGet\Packages even when it isn't on a non-interactive process's PATH
    # (the runner service caches PATH from before winget ran). Prefer it over
    # the stripped CapCut/bundled builds, which reject `-crf` etc.
    winget_pkgs = Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Packages"
    if winget_pkgs.exists():
        for ff in sorted(winget_pkgs.glob("Gyan.FFmpeg*/**/bin/ffmpeg.exe"), reverse=True):
            return str(ff)
    capcut_root = Path.home() / "AppData" / "Local" / "CapCut" / "Apps"
    if capcut_root.exists():
        for ff in capcut_root.glob("*/ffmpeg.exe"):
            return str(ff)
    bundled = REPO_ROOT / "node_modules" / "@ffmpeg-installer" / "win32-x64" / "ffmpeg.exe"
    if bundled.exists():
        return str(bundled)
    return "ffmpeg"


def _resolve_ffprobe() -> str:
    on_path = shutil.which("ffprobe")
    if on_path:
        return on_path
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


# YouTube "player clients" to try, in order. Each is a different API surface
# YouTube exposes; crucially, several of them do NOT trigger the "Sign in to
# confirm you're not a bot" gate that blocks the default `web` client from a
# datacenter IP (GitHub Actions). Trying them in turn means we download WITHOUT
# needing login cookies — which rot every week or two and were the real cause
# of rows being skipped "no video found". Order = most-reliable-cookieless first.
#   tv          — the living-room client; very lenient, rarely bot-gated.
#   ios / android — mobile app clients; separate quota, usually cookieless-ok.
#   web_safari  — desktop Safari surface; sometimes works when `web` is gated.
# `web` is intentionally LAST (and only with cookies) because it's the one that
# bot-blocks. See yt-dlp wiki: Extractors#youtube player_client.
_YT_CLIENTS_COOKIELESS = ("tv", "ios", "android", "web_safari")
_YT_CLIENTS_WITH_COOKIES = ("web", "tv", "ios")


def _ytdlp_base_opts(dest: Path) -> dict:
    """Shared yt-dlp options for every client attempt."""
    opts = {
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
        # YouTube gates real format URLs behind a JS "n challenge". yt-dlp
        # solves it with a JS runtime (Deno) + the EJS solver from GitHub.
        # Without this, only storyboard images are offered.
        "remote_components": ["ejs:github"],
    }
    ff = _resolve_ffmpeg()
    if ff and ff != "ffmpeg":
        # yt-dlp merges video+audio with ffmpeg; point it at the resolved
        # binary so local runs (no ffmpeg on PATH) work like CI does.
        opts["ffmpeg_location"] = ff
    return opts


def _is_bot_block(exc: Exception) -> bool:
    """True when an error is YouTube's anti-bot gate (vs. a genuinely dead
    video, geo-block, etc.) — those are the ones a different client can fix."""
    msg = str(exc).lower()
    return (
        "confirm you" in msg          # "Sign in to confirm you're not a bot"
        or "not a bot" in msg
        or "sign in to confirm" in msg
        or "requested format is not available" in msg  # client saw no real fmt
        or "unable to extract" in msg
    )


def _ytdlp_download(url: str, dest: Path) -> None:
    """Download the best portrait-leaning MP4 via yt-dlp, trying multiple
    YouTube player clients so we don't depend on (rot-prone) login cookies.

    Strategy:
      1. Try a sequence of COOKIELESS clients (tv/ios/android/web_safari).
         Most YouTube videos download from at least one of these without any
         login — so a missing/expired cookie no longer means "no video".
      2. If cookies ARE present, also try the cookie'd `web` client (some
         age-gated/region clips still need it). Cookies are now a BONUS, not a
         requirement.
    A bot-block on one client is retried on the next; a non-bot error (truly
    dead/removed video) stops early so we don't waste time on hopeless URLs.
    """
    try:
        from yt_dlp import YoutubeDL  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "yt-dlp is required to download YouTube URLs. "
            "Run: pip install -r requirements.txt"
        ) from exc

    dest.parent.mkdir(parents=True, exist_ok=True)

    cookiefile = _youtube_cookiefile()

    # Build the ordered list of (client, use_cookies) attempts.
    attempts: list[tuple[str, bool]] = [
        (c, False) for c in _YT_CLIENTS_COOKIELESS
    ]
    if cookiefile:
        log.info("YouTube cookies found (%s) — adding cookie'd clients as "
                 "backup attempts.", cookiefile)
        # Append cookie'd clients we haven't already tried cookieless.
        for c in _YT_CLIENTS_WITH_COOKIES:
            attempts.append((c, True))
    else:
        log.info("No YouTube cookies — relying on cookieless clients "
                 "(%s). This is expected and fine.",
                 ", ".join(_YT_CLIENTS_COOKIELESS))

    last_exc: Exception | None = None
    for client, use_cookies in attempts:
        opts = _ytdlp_base_opts(dest)
        opts["extractor_args"] = {"youtube": {"player_client": [client]}}
        if use_cookies and cookiefile:
            opts["cookiefile"] = cookiefile
        # A stale partial download from a failed client must not poison the
        # next attempt — yt-dlp would otherwise resume a 0-byte/partial file.
        for leftover in dest.parent.glob(dest.name + "*"):
            try:
                leftover.unlink()
            except OSError:
                pass
        try:
            log.info("yt-dlp attempt: client=%s cookies=%s", client, use_cookies)
            with YoutubeDL(opts) as ydl:
                ydl.download([url])
            if dest.exists() and dest.stat().st_size > 0:
                log.info("yt-dlp SUCCESS via client=%s%s", client,
                         " (with cookies)" if use_cookies else "")
                return
            # Downloaded "successfully" but produced nothing usable — treat as
            # a soft failure and try the next client.
            log.warning("client=%s produced no file — trying next client.",
                        client)
        except Exception as exc:  # noqa: BLE001 — we classify + continue
            last_exc = exc
            if _is_bot_block(exc):
                log.warning("client=%s bot-blocked/no-format — trying next.",
                            client)
                continue
            # A non-bot error (private/removed/geo video) won't be fixed by
            # another client. Stop now with a clear message.
            log.error("client=%s hit a non-recoverable error: %s", client, exc)
            raise

    raise RuntimeError(
        f"All YouTube clients failed to download {url}. "
        f"Last error: {last_exc}"
    )


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
