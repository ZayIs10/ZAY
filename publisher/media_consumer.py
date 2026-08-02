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
    # Route direct downloads through the residential proxy too when PROXY_URL is
    # set (same reason as yt-dlp — see _ytdlp_base_opts). No-op when unset.
    proxy = os.environ.get("PROXY_URL", "").strip()
    proxies = {"http": proxy, "https": proxy} if proxy else None
    try:
        r = requests.get(url, timeout=60, stream=True, proxies=proxies)
    except requests.exceptions.ProxyError as exc:
        if _is_proxy_exhausted(exc):
            raise ProxyExhaustedError(
                f"Residential proxy out of traffic (407 TRAFFIC_EXHAUSTED) "
                f"while fetching {url}."
            ) from exc
        raise
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


def _ytdlp_base_opts(dest: Path, section_seconds: float | None = None) -> dict:
    """Shared yt-dlp options for every client attempt.

    `section_seconds`: if set, download ONLY the first N seconds of the video
    instead of the whole thing. The reel only ever uses the START of the source
    clip (the compositor trims to <=60s from the beginning), so for a 10-minute
    source this fetches ~60s and skips the rest — a big bandwidth saving with
    ZERO quality loss. This matters whenever the download is metered: it makes
    a future residential proxy nearly free, and it speeds up every build today.
    Implemented via yt-dlp `download_ranges`, which makes the DASH downloader
    fetch only the fragments covering [0, N]; the compositor still does the
    exact final cut, so frame-accurate boundaries aren't needed here.
    """
    opts = {
        # Permissive: any video+audio, merged to mp4. The strict ext=mp4
        # filter could leave "Requested format is not available" when the
        # chosen player client only exposes webm/av1 streams.
        # Cap height at 1440p: the reel output is 1080x1920, so a 4K source is
        # pure wasted bandwidth (invisible once centre-cropped to 1080 wide) —
        # 1440p already gives ample detail. Falls back to best if a client
        # exposes nothing <=1440p, so format selection never fails.
        "format": "bv*[height<=1440]+ba/b[height<=1440]/bv*+ba/b",
        "outtmpl": str(dest),
        "quiet": True,
        "no_warnings": True,
        "merge_output_format": "mp4",
        "retries": 5,
        "fragment_retries": 5,
        # Fail a hung/blocked attempt FAST. Without this a proxy-blocked HD
        # fragment fetch hangs ~60s per client before erroring — across 5
        # source URLs × several clients that was the ~10-minute build stall.
        # 20s is plenty for a real fragment; a slower one just retries.
        "socket_timeout": 20,
        "hls_prefer_native": True,
        # YouTube gates real format URLs behind a JS "n challenge". yt-dlp
        # solves it with a JS runtime (Deno) + the EJS solver from GitHub.
        # Without this, only storyboard images are offered.
        "remote_components": ["ejs:github"],
    }
    proxy = os.environ.get("PROXY_URL", "").strip()

    # ---- Section download works UNDER THE PROXY TOO (re-verified 2026-08-02).
    # History: `download_ranges` makes yt-dlp hand the fetch to ffmpeg
    # (FFmpegFD), and the June exit-251 diagnosis assumed that ffmpeg ignored
    # the proxy for HTTPS — so ranges were dropped whenever PROXY_URL was set
    # and every proxied build silently pulled the WHOLE video (68-460 MB per
    # topic once we pivoted to long tutorials). That burned the entire $5
    # DataImpulse balance in ~a month (407 TRAFFIC_EXHAUSTED, 2026-08-02).
    #
    # The assumption is wrong for our ffmpeg builds: yt-dlp's FFmpegFD exports
    # HTTP_PROXY/http_proxy into the ffmpeg subprocess env, and ffmpeg's
    # http.c consults that env var for BOTH http and https (CONNECT tunnel).
    # Proven with the dead proxy as a tracer — ffmpeg reported the proxy's
    # "407" instead of connecting direct — on winget ffmpeg 8 (local) AND
    # apt ffmpeg on ubuntu-latest (CI probe run 30729218407).
    #
    # So: ALWAYS request the ranged download when the caller only needs the
    # opening (~60s ≈ 10-25 MB instead of the full video). _ytdlp_download
    # additionally retries a failed ranged attempt whole-file under the proxy,
    # so even if a specific stream trips ffmpeg (the old exit-251 family) the
    # build degrades to the previous whole-file behavior instead of skipping.
    if section_seconds and section_seconds > 0:
        # download_range_func lives in yt_dlp.utils; import lazily so
        # this module still loads without yt-dlp.
        from yt_dlp.utils import download_range_func  # type: ignore
        opts["download_ranges"] = download_range_func(
            None, [(0.0, float(section_seconds))])

    # Residential proxy (DataImpulse). When PROXY_URL is set, route every
    # download through it so the build can run on GitHub's CLOUD runners — whose
    # datacenter IPs YouTube permanently bot-blocks — with the user's PC OFF.
    if proxy:
        # ONLY yt-dlp gets the proxy (via this opt). Do NOT set http(s)_proxy
        # in os.environ — that routes EVERY Python HTTPS call through the
        # residential proxy, including Google Sheets / Drive / Instagram, and
        # the YouTube-unblocking proxy drops those hosts ("RemoteDisconnected"),
        # crashing the sheet write at the end of the build. The video+audio
        # merge is local-file-only (no network), so it never needs the proxy.
        opts["proxy"] = proxy
    ff = _resolve_ffmpeg()
    if ff and ff != "ffmpeg":
        # yt-dlp merges video+audio with ffmpeg; point it at the resolved
        # binary so local runs (no ffmpeg on PATH) work like CI does.
        opts["ffmpeg_location"] = ff
    return opts


class ProxyExhaustedError(RuntimeError):
    """The residential proxy (PROXY_URL) rejected the tunnel because the
    account's TRAFFIC balance is used up — DataImpulse answers every CONNECT
    with "407 TRAFFIC_EXHAUSTED". This is an INFRASTRUCTURE outage, not a
    property of the video: no client, cookie, or backup URL can succeed until
    the account is topped up. Callers must NOT treat it as "no usable video"
    (which burns the topic as Skipped) — park the row and retry later
    (see publisher/proxy_recovery.py)."""


def _is_proxy_exhausted(exc: Exception) -> bool:
    msg = str(exc).lower()
    return (
        "traffic_exhausted" in msg
        or ("407" in msg and "tunnel connection failed" in msg)
        or ("407" in msg and "proxyerror" in msg)
    )


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


def _ytdlp_download(url: str, dest: Path,
                    section_seconds: float | None = None) -> None:
    """Download the best portrait-leaning MP4 via yt-dlp, trying multiple
    YouTube player clients so we don't depend on (rot-prone) login cookies.

    `section_seconds` (optional) limits the download to the first N seconds of
    the video — see _ytdlp_base_opts. Pass it whenever the caller only needs the
    opening of the clip (the reel always does) to avoid pulling the whole file.

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

    if os.environ.get("PROXY_URL", "").strip():
        # Never log the proxy URL itself — it contains the login:password.
        log.info("PROXY_URL set — routing YouTube download through residential "
                 "proxy (cloud-runner / laptop-off mode).")

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

    proxied = bool(os.environ.get("PROXY_URL", "").strip())
    last_exc: Exception | None = None
    for client, use_cookies in attempts:
        # Per client: try the cheap RANGED download first (only the opening
        # section — what the reel actually uses). Under the metered proxy,
        # if the ranged attempt dies for a reason that is neither bot-block
        # nor proxy-exhaustion (e.g. a stream that trips ffmpeg's range
        # fetch — the old exit-251 family), fall back to the WHOLE-file
        # download for the same client: never worse than the pre-range
        # behavior, just costlier. Without a proxy the whole-file retry is
        # pointless (nothing metered changed), so keep the old semantics.
        plans: list[float | None] = [section_seconds]
        if proxied and section_seconds:
            plans.append(None)
        for plan_seconds in plans:
            ranged_plan = plan_seconds is not None and plan_seconds > 0
            opts = _ytdlp_base_opts(dest, section_seconds=plan_seconds)
            opts["extractor_args"] = {"youtube": {"player_client": [client]}}
            if use_cookies and cookiefile:
                opts["cookiefile"] = cookiefile
            # A stale partial download from a failed attempt must not poison
            # the next one — yt-dlp would otherwise resume a 0-byte/partial
            # file.
            for leftover in dest.parent.glob(dest.name + "*"):
                try:
                    leftover.unlink()
                except OSError:
                    pass
            try:
                log.info("yt-dlp attempt: client=%s cookies=%s ranged=%s",
                         client, use_cookies, ranged_plan)
                with YoutubeDL(opts) as ydl:
                    ydl.download([url])
                if dest.exists() and dest.stat().st_size > 0:
                    log.info(
                        "yt-dlp SUCCESS via client=%s%s (%s, %.1f MB)",
                        client, " (with cookies)" if use_cookies else "",
                        "ranged" if ranged_plan else "whole-file",
                        dest.stat().st_size / 1e6)
                    return
                # Downloaded "successfully" but produced nothing usable —
                # treat as a soft failure and try the next client.
                log.warning("client=%s produced no file — trying next "
                            "client.", client)
                break
            except Exception as exc:  # noqa: BLE001 — we classify + continue
                last_exc = exc
                if _is_proxy_exhausted(exc):
                    # The proxy account is out of traffic — EVERY client and
                    # every backup URL goes through the same dead tunnel, so
                    # trying more is pure log spam. Surface the real problem
                    # immediately.
                    raise ProxyExhaustedError(
                        f"Residential proxy out of traffic "
                        f"(407 TRAFFIC_EXHAUSTED) while downloading {url}. "
                        f"Top up the DataImpulse account; parked rows "
                        f"auto-rebuild via proxy_recovery."
                    ) from exc
                if _is_bot_block(exc):
                    # A bot-gated client is gated ranged or not — move on to
                    # the next client (staying ranged, i.e. cheap).
                    log.warning("client=%s bot-blocked/no-format — trying "
                                "next.", client)
                    break
                if ranged_plan and len(plans) > 1:
                    log.warning(
                        "client=%s RANGED download failed (%s) — retrying "
                        "same client whole-file through the proxy.",
                        client, exc)
                    continue
                # A non-bot error (private/removed/geo video) won't be fixed
                # by another client. Stop now with a clear message.
                log.error("client=%s hit a non-recoverable error: %s",
                          client, exc)
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

    # Each beat is cut from the START of the source (see _ffmpeg_cut_to_beat),
    # so the deepest any beat reaches is the longest single beat. Download only
    # that much (+2s buffer) rather than the whole video.
    section = (max(durations) + 2.0) if durations else None
    if _is_youtube(url):
        log.info("Downloading YouTube clip via yt-dlp: %s", url)
        _ytdlp_download(url, raw, section_seconds=section)
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
        # The reel uses at most `max_seconds` from the START of the clip (the
        # compositor trims to that), so only download that opening section — a
        # 10-min source no longer pulls 10 min of data. +2s is a safety buffer.
        _ytdlp_download(url, raw, section_seconds=max_seconds + 2.0)
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
