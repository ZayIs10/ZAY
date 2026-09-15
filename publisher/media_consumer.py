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
# Re-verified 2026-09-11 from the home IP (yt-dlp 2026.8.19) with the PO-token
# provider running (publisher/pot_provider.py) — without a PO token EVERY
# client below fails or returns a stub:
#   mweb / web_embedded — serve the real DASH file (398+251, 34 MB/9 min). FIRST.
#   tv          — "The page needs to be reloaded" challenge (no formats) today,
#                 but historically the most lenient client; keep as a rotation.
#   ios / web_safari — "Requested format is not available" today.
#   android_vr  — lists formats but the media URL 403s.
#   android     — returns a 217 KB STUB (see _looks_like_stub). LAST.
# `web` only with cookies — it's the one that bot-blocks cookieless.
# See yt-dlp wiki: Extractors#youtube player_client.
_YT_CLIENTS_COOKIELESS = ("mweb", "web_embedded", "tv", "ios", "web_safari",
                          "android_vr", "android")
_YT_CLIENTS_WITH_COOKIES = ("web", "mweb", "tv")

# Whole-file ceiling on the FREE (un-proxied, home-IP) path. Bandwidth is
# unmetered there, so this only guards disk/time against a multi-hour source:
# yt-dlp refuses up front from the format's reported size, and the ranged
# plan then runs instead. 800 MB ≈ a 2-hour 1080p talk.
UNPROXIED_WHOLEFILE_MAX_BYTES = 800_000_000

# HARD SPEND CEILING for the whole-file fallback when downloading through the
# METERED residential proxy. A ranged download is ~2-4 MB, but the whole-file
# retry below has no natural size limit — a long tutorial is 68-460 MB, and the
# retry used to be offered once PER CLIENT (up to 7), so a single unlucky build
# could pull multiple GB. That is precisely how the $5 DataImpulse balance
# vanished in a month (407 TRAFFIC_EXHAUSTED, 2026-08-02) and the current
# Thordata plan is only 1 GB. yt-dlp checks this against the format's reported
# size and refuses BEFORE spending anything, so an oversized source costs ~0
# instead of the whole file. 60 MB comfortably covers any short/normal source
# while making a runaway drain impossible.
PROXIED_WHOLEFILE_MAX_BYTES = 60_000_000


def _ytdlp_base_opts(dest: Path, section_seconds: float | None = None,
                     section_start: float = 0.0) -> dict:
    """Shared yt-dlp options for every client attempt.

    `section_seconds`: if set, download ONLY N seconds of the video instead of
    the whole thing. The reel only ever uses one contiguous stretch of the
    source clip, so for a 10-minute source this fetches ~60s and skips the
    rest — a big bandwidth saving with ZERO quality loss. This matters
    whenever the download is metered: it makes a residential proxy nearly
    free, and it speeds up every build today. Implemented via yt-dlp
    `download_ranges`, which makes the DASH downloader fetch only the
    fragments covering the range; the compositor still does the exact final
    cut, so frame-accurate boundaries aren't needed at start 0.

    `section_start`: where the range begins (motivation reels cut the
    HIGHLIGHT out of a long speech, not the opening — see
    speech_captions.best_window). A non-zero start additionally forces
    keyframes at the cut (a re-encode of just the section), because a plain
    copy starts at the nearest earlier keyframe and the word-timed captions
    would drift out of sync by that unknown offset.
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
        start = max(0.0, float(section_start))
        opts["download_ranges"] = download_range_func(
            None, [(start, start + float(section_seconds))])
        if start > 0:
            # Frame-accurate cut: without this the file starts at the nearest
            # keyframe BEFORE `start` (unknown offset) and every word-timed
            # caption lands early by that much.
            opts["force_keyframes_at_cuts"] = True

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
        # Metered path + no range = the whole-file fallback. Cap it so a long
        # source can never silently drain the balance (see the constant).
        if not (section_seconds and section_seconds > 0):
            opts["max_filesize"] = PROXIED_WHOLEFILE_MAX_BYTES
    elif not (section_seconds and section_seconds > 0):
        # Free path whole-file: only a sanity ceiling (see the constant).
        opts["max_filesize"] = UNPROXIED_WHOLEFILE_MAX_BYTES
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
        # 2026-09: YouTube's newer challenge response on the tv client.
        # Another client (ios/android) usually serves the same video fine, so
        # this must rotate, not abort the whole chain on attempt #1.
        or "page needs to be reloaded" in msg
        # 2026-09-11: without a PO token YouTube 403s the real media URL on
        # several clients (android_vr, the default rotation). Another client
        # (mweb/web_embedded via the provider) serves it — rotate, don't abort.
        or "http error 403" in msg
    )


def _is_dead_video(exc: Exception) -> bool:
    """True ONLY for errors that mean the VIDEO itself is gone or locked —
    the cases no client, cookie or token can fix, so trying more is a waste.
    Everything else (ffmpeg range hiccups, stub files, transient network) is
    treated as recoverable and rotates to the next plan/client. This used to
    be the inverse ("anything not a bot-block is fatal") and that is exactly
    how the Denzel row died on 2026-09-11: attempt #3's ffmpeg error aborted
    the whole chain before the clients that would have worked were tried."""
    msg = str(exc).lower()
    return (
        "video unavailable" in msg
        or "private video" in msg
        or "has been removed" in msg
        or "this video is not available" in msg
        or "not available in your country" in msg
        or "account associated with this video has been terminated" in msg
        or "is not a valid url" in msg
    )


def _looks_like_stub(path: Path) -> str | None:
    """Return a reason string when `path` is NOT a real playable video.

    YouTube's anti-bot response (2026-09) is not always an error: the
    `android` / `mweb` / `web_embedded` clients without a PO token hand yt-dlp
    a 217 KB MP4 whose header claims the full 9-minute duration but whose
    data track is empty. yt-dlp reports success, the old `size > 0` check
    passed it, and the build only found out at render time (or never).
    Two cheap checks catch it: implausible bitrate (< 40 kbps for something
    claiming to be video), and ffmpeg failing to decode a couple of seconds.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return "file missing"
    if size == 0:
        return "empty file"
    from publisher.compositor import probe_duration  # noqa: E402
    try:
        dur = probe_duration(path)
    except Exception:  # noqa: BLE001 — probe failure = unreadable file
        return "ffprobe cannot read the file"
    if dur and dur > 0:
        kbps = size * 8 / dur / 1000
        if kbps < 40:
            return (f"stub: {size / 1e3:.0f} KB for a claimed {dur:.0f}s "
                    f"({kbps:.1f} kbps)")
    ss = 5.0 if (dur or 0) > 12 else 0.0
    proc = subprocess.run(
        [_resolve_ffmpeg(), "-v", "error", "-ss", f"{ss:.1f}", "-i", str(path),
         "-t", "2", "-f", "null", "-"],
        capture_output=True, text=True)
    err = (proc.stderr or "").lower()
    if "invalid data" in err or "partial file" in err or proc.returncode != 0:
        return f"ffmpeg cannot decode it: {err.strip().splitlines()[-1][:120] if err.strip() else 'exit ' + str(proc.returncode)}"
    return None


def _ytdlp_download(url: str, dest: Path,
                    section_seconds: float | None = None,
                    section_start: float = 0.0) -> None:
    """Download the best portrait-leaning MP4 via yt-dlp, trying multiple
    YouTube player clients so we don't depend on (rot-prone) login cookies.

    `section_seconds` (optional) limits the download to N seconds of the
    video from `section_start` — see _ytdlp_base_opts.

    Strategy (re-worked 2026-09-11, see publisher/pot_provider.py):
      0. Make sure the PO-token provider is up. YouTube now demands a PO
         token on every client; without one downloads 403 or return stubs.
      1. Try clients in `_YT_CLIENTS_COOKIELESS` order; with cookies present
         also the cookie'd ones. A bot-block / 403 / no-format on one client
         rotates to the next.
      2. Per client, the download PLAN depends on the network:
         - FREE home IP (no PROXY_URL): WHOLE FILE FIRST. It uses yt-dlp's
           native downloader (fast: 34 MB in seconds, tokens attached) and the
           caller cuts locally (motivation reels: cut_section; tweet-card:
           the compositor trims). The RANGED plan is only the fallback — it
           hands the fetch to ffmpeg, which took 367 s for 102 s of video and
           is the path that 403s / hits stubs.
         - METERED proxy: RANGED FIRST (cheap), then ONE capped whole-file
           retry per build (unchanged spend rules).
      3. Every produced file is validated (`_looks_like_stub`) — a 217 KB
         "success" is a failure and rotates.
      4. Only a genuinely DEAD video (`_is_dead_video`) aborts early.
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

    # Step 0 — PO token provider (best-effort, never raises).
    from publisher import pot_provider  # noqa: E402
    if not pot_provider.ensure_running():
        log.warning("Downloading WITHOUT a PO-token provider — expect 403s / "
                    "stub files on most clients.")

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
    # Under the METERED proxy the whole-file fallback is offered AT MOST ONCE
    # PER BUILD, not once per client. It used to be per-client, so a source
    # that trips ffmpeg's range fetch on every client could trigger up to 7
    # whole-video downloads in a single build (0.5-3.2 GB through a metered
    # proxy). One attempt is enough to prove whether whole-file helps.
    wholefile_spent = False
    wants_section = bool(section_seconds and section_seconds > 0)
    for client, use_cookies in attempts:
        if proxied:
            # Metered: cheap RANGED first; ONE capped whole-file retry/build.
            plans: list[float | None] = [section_seconds]
            if wants_section and not wholefile_spent:
                plans.append(None)
        else:
            # Free home IP: WHOLE FILE first (native downloader, fast,
            # PO-token-aware); ranged (ffmpeg fetch) only as the fallback.
            plans = [None]
            if wants_section:
                plans.append(section_seconds)
        for plan_seconds in plans:
            ranged_plan = plan_seconds is not None and plan_seconds > 0
            if not ranged_plan and proxied:
                # Consume the single per-build whole-file allowance up front, so
                # it is spent even if this attempt throws.
                wholefile_spent = True
                log.warning(
                    "Ranged download failed — spending this build's ONE "
                    "whole-file retry (capped at %d MB) through the metered "
                    "proxy. Later clients get ranged attempts only.",
                    PROXIED_WHOLEFILE_MAX_BYTES // 1_000_000)
            opts = _ytdlp_base_opts(dest, section_seconds=plan_seconds,
                                    section_start=section_start)
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
                    why = _looks_like_stub(dest)
                    if why is None:
                        log.info(
                            "yt-dlp SUCCESS via client=%s%s (%s, %.1f MB)",
                            client, " (with cookies)" if use_cookies else "",
                            "ranged" if ranged_plan else "whole-file",
                            dest.stat().st_size / 1e6)
                        return
                    # YouTube's silent anti-bot answer: a "successful" file
                    # with no usable video in it. Same as a bot-block.
                    last_exc = RuntimeError(
                        f"client={client} returned an unplayable file ({why})")
                    log.warning("client=%s returned an unplayable file (%s) — "
                                "trying next client.", client, why)
                    break
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
                if _is_dead_video(exc):
                    # Gone/private/geo-locked: no client can fix it. Stop now
                    # with a clear message so the row is skipped honestly.
                    log.error("client=%s: video is unavailable — %s",
                              client, exc)
                    raise
                if _is_bot_block(exc):
                    # A bot-gated client is gated ranged or not — move on to
                    # the next client.
                    log.warning("client=%s bot-blocked/no-format — trying "
                                "next.", client)
                    break
                if len(plans) > 1 and plan_seconds is plans[0]:
                    # First plan for this client died for some other reason
                    # (ffmpeg range fetch, size cap, transient) — try the
                    # other plan on the same client before moving on.
                    log.warning(
                        "client=%s %s download failed (%s) — retrying the "
                        "same client %s.",
                        client, "RANGED" if ranged_plan else "whole-file",
                        str(exc)[:160],
                        "whole-file" if ranged_plan else "ranged")
                    continue
                # Both plans failed on this client. NOT fatal any more: the
                # old code raised here and killed the whole chain on attempt
                # #3 (2026-09-11) — rotate to the next client instead.
                log.warning("client=%s failed (%s) — trying next client.",
                            client, str(exc)[:160])
                break

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


def cut_section(src: Path, start_seconds: float, duration_s: float) -> Path:
    """Cut [start, start+duration] out of a LOCAL file, frame-accurate
    (re-encode; -ss before -i with an output re-encode seeks exactly).
    Needed when the proxied whole-file fallback fetched the ENTIRE video but
    the motivation reel wants a mid-video highlight whose captions are timed
    from `start_seconds`. Replaces src with the cut (same contract as the
    ranged download: file t=0 == highlight start)."""
    dest = src.with_name(src.stem + "_sect.mp4")
    cmd = [
        _resolve_ffmpeg(), "-y", "-loglevel", "warning",
        "-ss", f"{start_seconds:.3f}", "-i", str(src),
        "-t", f"{duration_s:.2f}",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(dest),
    ]
    proc = subprocess.run(cmd)
    if proc.returncode != 0 or not dest.exists() or dest.stat().st_size == 0:
        raise RuntimeError(
            f"ffmpeg failed to cut section {start_seconds:.1f}s+{duration_s:.1f}s "
            f"from {src} (exit {proc.returncode})"
        )
    src.unlink(missing_ok=True)
    dest.rename(src)
    log.info("Local section cut: %s now starts at %.1fs of the source "
             "(%.1f MB).", src.name, start_seconds, src.stat().st_size / 1e6)
    return src


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


def fetch_single_clip(url: str, slug: str, *, max_seconds: float = 60.0,
                      start_seconds: float = 0.0) -> Path:
    """Download `url` once, return ONE mp4 path. Used by the tweet-card
    reel pipeline (the static caption + variable-length source video
    format) where downstream wants the whole clip, not per-beat splits.

    `start_seconds` shifts the downloaded section into the video (motivation
    reels cut the transcript-scored highlight, not the opening). Only applies
    to YouTube sources; a direct URL is always fetched whole.

    The clip is NOT re-encoded here — duration capping and scale/crop
    happen later in `publisher/compositor.py` so the source bytes stay
    on disk for retry / debugging without burning a transcode pass.
    (Exception: a non-zero start forces keyframes at the cut inside yt-dlp —
    see _ytdlp_base_opts — because caption sync needs an exact start.)
    """
    CLIP_DIR.mkdir(parents=True, exist_ok=True)
    raw = CLIP_DIR / f"_single_{slug}.mp4"

    if _is_youtube(url):
        log.info("Downloading YouTube clip via yt-dlp: %s", url)
        # The reel uses at most `max_seconds` of the clip (the compositor
        # trims to that), so only download that section — a 10-min source no
        # longer pulls 10 min of data. +2s is a safety buffer.
        _ytdlp_download(url, raw, section_seconds=max_seconds + 2.0,
                        section_start=start_seconds)
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
