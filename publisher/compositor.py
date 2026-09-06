"""ffmpeg compositor for the @execute-style tweet-card reel.

Stacks three layers onto a 1080x1920 black canvas:
  1. Black background (full duration)
  2. Lower rect at (60, 820), 960x900: a 1-second poster image
     followed by the source video, both scaled+center-cropped.
  3. Tweet-card PNG at (40, 60), shown the full duration (static).

Optionally (build(..., hook_video=...)) the reel OPENS with a viral hook
clip from viralhooks.org (see publisher/hook_opener.py): the whole hook
plays full-screen (cover-cropped to the canvas) with NO overlay — the
tweet card only appears when the body starts (user's call, 2026-07-19:
keep the hook clean). Then it hard-cuts into the body above.

Optionally (build(..., cta_endcard=...)) the reel CLOSES with the
"comment SEND" call-to-action animation (see publisher/cta_endcard.py).

The body's cap shrinks by the hook's and end-card's durations so the
total still respects max_seconds.

Audio: if the source video HAS an audio track, the reel keeps it —
preceded by `preview_seconds` of silence so it lines up with the poster
intro. If the source has no audio, the reel is silent (-an) — unless a
hook is prepended, in which case every segment carries an (aac 44.1k
stereo) track, silent if needed, so the concat can't desync. Total
duration = preview_seconds + source video duration, capped at
max_seconds (Instagram Reels limit).

This is the only place in the codebase that drives ffmpeg with a
multi-input filter_complex. Keep the graph here, not inline in
tweet_card_reel.py, so it stays testable in isolation.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from publisher.media_consumer import _resolve_ffmpeg, _resolve_ffprobe  # noqa: E402

log = logging.getLogger("compositor")

CANVAS_W = 1080
CANVAS_H = 1920
CARD_X = 40
CARD_Y = 60
VIDEO_X = 60
VIDEO_Y = 820
VIDEO_W = 960
VIDEO_H = 900


# How the source clip is fitted into the VIDEO_W x VIDEO_H window.
#
# We FIT THE WHOLE FRAME (letterbox) — never crop the sides. A YouTube clip is
# usually 16:9 (very wide); the window is near-square (960x900). Filling the
# window (scale-to-cover + crop) chopped ~40% off the left/right, cutting out
# people standing to one side and on-screen text near the edges — the exact
# bug the user reported. So instead we scale the whole frame DOWN to fit inside
# the window (force_original_aspect_ratio=decrease) and pad the leftover space
# with black bars, clip centered. Nothing on the sides is ever lost.
_FIT_FILTER = (
    f"scale={VIDEO_W}:{VIDEO_H}:force_original_aspect_ratio=decrease,"
    f"pad={VIDEO_W}:{VIDEO_H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1"
)


_DUR_RX = __import__("re").compile(
    r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)"
)


def _duration_via_ffmpeg(path: Path) -> float:
    """Parse Duration line from `ffmpeg -i` stderr.
    Used when ffprobe isn't on the system (CapCut's bundled
    ffmpeg ships without it)."""
    cmd = [_resolve_ffmpeg(), "-hide_banner", "-i", str(path)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    m = _DUR_RX.search(proc.stderr or "")
    if not m:
        raise RuntimeError(
            f"could not parse duration for {path} from ffmpeg stderr"
        )
    h, mn, s = m.groups()
    return int(h) * 3600 + int(mn) * 60 + float(s)


def probe_duration(path: Path) -> float:
    """Seconds of media in `path`. Prefers ffprobe; falls back to
    parsing `ffmpeg -i` stderr when ffprobe is unavailable."""
    probe = _resolve_ffprobe()
    cmd = [
        probe, "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json", str(path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        return _duration_via_ffmpeg(path)
    if proc.returncode != 0:
        return _duration_via_ffmpeg(path)
    data = json.loads(proc.stdout or "{}")
    dur = float(data.get("format", {}).get("duration", 0.0))
    if dur <= 0:
        return _duration_via_ffmpeg(path)
    return dur


def probe_dimensions(path: Path) -> tuple[int, int]:
    """(width, height) of the first video stream, or (0, 0) if unknowable."""
    probe = _resolve_ffprobe()
    cmd = [
        probe, "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "json", str(path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
        data = json.loads(proc.stdout or "{}")
        stream = (data.get("streams") or [{}])[0]
        return int(stream.get("width", 0)), int(stream.get("height", 0))
    except Exception:  # noqa: BLE001 — caller falls back to cover-crop
        return 0, 0


def _has_audio_via_ffmpeg(path: Path) -> bool:
    """Fallback audio-stream detection: look for an 'Audio:' stream line in
    `ffmpeg -i` stderr (used when ffprobe isn't installed)."""
    cmd = [_resolve_ffmpeg(), "-hide_banner", "-i", str(path)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return "Audio:" in (proc.stderr or "")


def has_audio(path: Path) -> bool:
    """True if `path` contains at least one audio stream. Prefers ffprobe;
    falls back to scanning `ffmpeg -i` stderr."""
    probe = _resolve_ffprobe()
    cmd = [
        probe, "-v", "error",
        "-select_streams", "a",
        "-show_entries", "stream=index",
        "-of", "json", str(path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        return _has_audio_via_ffmpeg(path)
    if proc.returncode != 0:
        return _has_audio_via_ffmpeg(path)
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return _has_audio_via_ffmpeg(path)
    return len(data.get("streams", [])) > 0


# Full-screen cover for the viral hook opener: fill the entire canvas and
# center-crop the overflow (house rule: fill the frame, never letterbox a
# vertical clip — the hooks are natively 1080x1920 so this is usually a no-op).
_COVER_FILTER = (
    f"scale={CANVAS_W}:{CANVAS_H}:force_original_aspect_ratio=increase,"
    f"crop={CANVAS_W}:{CANVAS_H},setsar=1"
)

# Cinematic grade for motivation-speech reels (user, 2026-09-04): the look
# the big motivation pages run — teal-pushed shadows, slightly warm
# highlights, raised contrast, pulled-back saturation, a soft vignette and
# light film grain. Free ffmpeg filters, no LUT files.
_CINE_GRADE = (
    "eq=contrast=1.12:saturation=0.85,"
    "colorbalance=rs=-0.05:gs=0.02:bs=0.10:rh=0.04:bh=-0.04,"
    "vignette=angle=PI/5,"
    "noise=alls=5:allf=t"
)

# A hook that leaves less than this for the body is skipped — the reel's
# actual content must always dominate the runtime.
_MIN_BODY_SECONDS = 6.0

# Motivation reels: zoom into the source by this factor (crop the outer edges,
# keep the aspect ratio). User, 2026-09-06: faces read too small at the raw
# original framing — punch in so people are seen clearly, without the full
# cover-crop that hid speakers entirely. 1.25 trims 10% off each edge.
_SPEECH_ZOOM = 1.25


def speech_caption_band_y(source_video: Path, band_h: int) -> int | None:
    """Top edge for the motivation reel's word-pop caption band: centered ON
    the footage for a non-vertical source, so our Anton captions sit exactly
    where source clips burn in their own captions and cover them (user,
    2026-09-06 — the Goggins clip's baked-in mid-frame captions clashed with
    ours). Returns None for a vertical/unknown source — the caller keeps its
    lower-third default."""
    src_w, src_h = probe_dimensions(source_video)
    fg_h = int(round(CANVAS_W * src_h / src_w / 2) * 2) if src_w else 0
    if 0 < fg_h < CANVAS_H - 120:
        fg_y = (CANVAS_H - fg_h) // 2
        return max(0, fg_y + (fg_h - band_h) // 2)
    return None


def build(
    card_png: Path,
    source_video: Path,
    poster_image: Path,
    out_path: Path,
    *,
    preview_seconds: float = 1.0,
    max_seconds: float = 60.0,
    hook_video: Path | None = None,
    cta_endcard: Path | None = None,
) -> Path:
    """Composite the reel and write `out_path`. Returns the output path.

    With `hook_video` set, the WHOLE hook clip plays first (full-screen,
    clean — no card). With `cta_endcard` set, the "comment SEND" call-to-
    action card is appended after the body. With neither, the output is
    byte-for-byte the same build as before those features existed.

    Both extras share the 60s cap with the body. If the body would be
    squeezed under `_MIN_BODY_SECONDS`, the HOOK is dropped first (it's
    decoration); the CTA is dropped only if that still isn't enough,
    because the CTA is the whole point of the reel converting.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if hook_video is None and cta_endcard is None:
        return _render_body(
            card_png, source_video, poster_image, out_path,
            preview_seconds=preview_seconds, max_seconds=max_seconds,
        )

    hook_dur = probe_duration(hook_video) if hook_video else 0.0
    cta_dur = probe_duration(cta_endcard) if cta_endcard else 0.0

    if max_seconds - hook_dur - cta_dur < _MIN_BODY_SECONDS and hook_video:
        log.warning(
            "Hook (%.1fs) + CTA (%.1fs) leave under %.0fs of the %.0fs cap "
            "for the body — dropping the HOOK.",
            hook_dur, cta_dur, _MIN_BODY_SECONDS, max_seconds,
        )
        hook_video, hook_dur = None, 0.0
    if max_seconds - cta_dur < _MIN_BODY_SECONDS and cta_endcard:
        log.warning(
            "CTA end-card (%.1fs) leaves under %.0fs of the %.0fs cap for "
            "the body — dropping the CTA too.",
            cta_dur, _MIN_BODY_SECONDS, max_seconds,
        )
        cta_endcard, cta_dur = None, 0.0
    if hook_video is None and cta_endcard is None:
        return _render_body(
            card_png, source_video, poster_image, out_path,
            preview_seconds=preview_seconds, max_seconds=max_seconds,
        )

    body_max = max_seconds - hook_dur - cta_dur
    log.info(
        "Segments: hook %.1fs + body <=%.1fs + CTA %.1fs (cap %.0fs)",
        hook_dur, body_max, cta_dur, max_seconds,
    )

    # Segment names deliberately do NOT end in '-tweet.mp4' so the Actions
    # artifact glob (renders/*-tweet.mp4) never picks up an intermediate.
    segments: list[Path] = []
    intermediates: list[Path] = []
    if hook_video is not None:
        hook_seg = out_path.with_name(out_path.stem + "_hookseg.mp4")
        _build_hook_segment(hook_video, hook_seg, seconds=hook_dur)
        segments.append(hook_seg)
        intermediates.append(hook_seg)

    body_seg = out_path.with_name(out_path.stem + "_bodyseg.mp4")
    # force_audio: the other segments always carry an audio track, so the
    # body must too (even if the source clip is silent) or concat desyncs.
    _render_body(
        card_png, source_video, poster_image, body_seg,
        preview_seconds=preview_seconds, max_seconds=body_max,
        force_audio=True,
    )
    segments.append(body_seg)
    intermediates.append(body_seg)

    if cta_endcard is not None:
        # Already encoded with this module's exact recipe by
        # publisher/cta_endcard.py, so it concatenates as a stream copy.
        segments.append(cta_endcard)

    _concat_copy(segments, out_path)
    for seg in intermediates:
        try:
            seg.unlink()
        except OSError:
            pass
    return out_path


def _build_hook_segment(
    hook_mp4: Path,
    out_path: Path,
    *,
    seconds: float,
) -> Path:
    """Render the opener: the whole hook clip cover-cropped to the full
    canvas, CLEAN — no tweet card (the card only appears once the body
    starts; user's call 2026-07-19). Keeps the hook's own audio (impact
    sounds are part of the attention grab; silence if the file has none).
    Encoded with the exact same params as the body so the final concat is
    a clean stream copy."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    keep_audio = has_audio(hook_mp4)

    video_graph = (
        f"[0:v]{_COVER_FILTER},fps=30,trim=duration={seconds:.2f},"
        f"setpts=PTS-STARTPTS[v]"
    )

    cmd = [
        _resolve_ffmpeg(), "-y", "-loglevel", "warning",
        "-i", str(hook_mp4),
    ]
    if not keep_audio:
        # Input 1: silence, so this segment still carries an audio track.
        cmd += [
            "-f", "lavfi", "-t", f"{seconds:.2f}",
            "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        ]

    if keep_audio:
        cmd += [
            "-filter_complex",
            video_graph
            + f";[0:a]atrim=duration={seconds:.2f},asetpts=PTS-STARTPTS[a]",
            "-map", "[v]", "-map", "[a]",
        ]
    else:
        cmd += ["-filter_complex", video_graph, "-map", "[v]", "-map", "1:a"]

    cmd += [
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-r", "30", "-g", "30", "-keyint_min", "30",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
        "-t", f"{seconds:.2f}",
        str(out_path),
    ]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        log.error("ffmpeg stderr:\n%s", proc.stderr)
        raise RuntimeError(f"hook segment failed (exit {proc.returncode})")
    return out_path


def _concat_copy(segments: list[Path], out_path: Path) -> Path:
    """Join segments with the concat demuxer as a stream copy. All segments
    must share codec params (they do — one encode recipe everywhere)."""
    concat_list = out_path.with_name(out_path.stem + "_concat.txt")
    concat_list.write_text(
        "".join(f"file '{s.as_posix()}'\n" for s in segments),
        encoding="utf-8",
    )
    cmd = [
        _resolve_ffmpeg(), "-y", "-loglevel", "warning",
        "-f", "concat", "-safe", "0", "-i", str(concat_list),
        "-c", "copy", "-movflags", "+faststart",
        str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    try:
        concat_list.unlink()
    except OSError:
        pass
    if proc.returncode != 0:
        log.error("ffmpeg concat stderr:\n%s", proc.stderr)
        raise RuntimeError(f"hook concat failed (exit {proc.returncode})")
    return out_path


def _render_body(
    card_png: Path,
    source_video: Path,
    poster_image: Path,
    out_path: Path,
    *,
    preview_seconds: float = 1.0,
    max_seconds: float = 60.0,
    force_audio: bool = False,
) -> Path:
    """The original single-pass composite (poster intro + clip + card).
    `force_audio` guarantees an audio track on the output even when the
    source clip is silent — required whenever this segment will be
    concatenated after a hook segment."""
    out_path.parent.mkdir(parents=True, exist_ok=True)

    src_dur = probe_duration(source_video)
    total_dur = min(preview_seconds + src_dur, max_seconds)
    # If we hit the cap, the source plays for (max - preview) seconds.
    src_play_dur = max(0.5, total_dur - preview_seconds)

    log.info(
        "Composite: poster %.1fs + source %.1fs (cap %.1f) -> total %.1fs",
        preview_seconds, src_play_dur, max_seconds, total_dur,
    )

    crop_filter = _FIT_FILTER

    keep_audio = has_audio(source_video)
    log.info("Source audio: %s", "present -> kept" if keep_audio else "none -> silent")

    video_graph = (
        # Inputs:
        #   [0] color canvas, [1] poster image (looped),
        #   [2] source video (capped), [3] tweet-card PNG.
        f"[1:v]{crop_filter},fps=30,trim=duration={preview_seconds:.2f},"
        f"setpts=PTS-STARTPTS[poster];"
        f"[2:v]{crop_filter},fps=30,trim=duration={src_play_dur:.2f},"
        f"setpts=PTS-STARTPTS[clip];"
        f"[poster][clip]concat=n=2:v=1:a=0[rect];"
        f"[0:v][rect]overlay=x={VIDEO_X}:y={VIDEO_Y}:shortest=1[bg_rect];"
        f"[bg_rect][3:v]overlay=x={CARD_X}:y={CARD_Y}:format=auto[v]"
    )

    cmd = [
        _resolve_ffmpeg(), "-y", "-loglevel", "warning",
        # 0: black canvas, full duration.
        "-f", "lavfi", "-t", f"{total_dur:.2f}",
        "-i", f"color=c=black:s={CANVAS_W}x{CANVAS_H}:r=30",
        # 1: poster (loop infinitely; trim handles the 1s window).
        "-loop", "1", "-i", str(poster_image),
        # 2: source video.
        "-i", str(source_video),
        # 3: tweet card PNG.
        "-i", str(card_png),
    ]

    if keep_audio:
        # 4: silence to cover the poster intro so the source audio starts
        # exactly when the clip starts playing.
        cmd += [
            "-f", "lavfi", "-t", f"{preview_seconds:.2f}",
            "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        ]
        audio_graph = (
            f";[4:a]atrim=duration={preview_seconds:.2f},asetpts=PTS-STARTPTS[sil];"
            f"[2:a]atrim=duration={src_play_dur:.2f},asetpts=PTS-STARTPTS[srca];"
            f"[sil][srca]concat=n=2:v=0:a=1[a]"
        )
        cmd += [
            "-filter_complex", video_graph + audio_graph,
            "-map", "[v]", "-map", "[a]",
        ]
    elif force_audio:
        # 4: full-duration silence — a segment that follows a hook must
        # still carry an audio track or the final concat desyncs.
        cmd += [
            "-f", "lavfi", "-t", f"{total_dur:.2f}",
            "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        ]
        cmd += [
            "-filter_complex", video_graph,
            "-map", "[v]", "-map", "4:a",
        ]
    else:
        cmd += [
            "-filter_complex", video_graph,
            "-map", "[v]",
        ]

    cmd += [
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-r", "30", "-g", "30", "-keyint_min", "30",
        "-movflags", "+faststart",
    ]
    if keep_audio or force_audio:
        # 44.1k stereo everywhere so a hook segment + body concat cleanly.
        cmd += ["-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2"]
    else:
        cmd += ["-an"]
    cmd += [
        "-t", f"{total_dur:.2f}",
        str(out_path),
    ]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        log.error("ffmpeg stderr:\n%s", proc.stderr)
        raise RuntimeError(
            f"ffmpeg composite failed (exit {proc.returncode})"
        )

    return out_path


def build_speech(
    source_video: Path,
    scrim_png: Path,
    captions_ffconcat: Path,
    out_path: Path,
    *,
    band_y: int,
    max_seconds: float = 60.0,
    cta_endcard: Path | None = None,
) -> Path:
    """Composite a MOTIVATION-SPEECH reel: the source clip at its ORIGINAL
    aspect ratio (user, 2026-09-04 — cover-cropping hid the speaker's face),
    punched in by _SPEECH_ZOOM and scaled to full width, centered over a
    blurred + darkened cover-crop of itself filling the rest of the canvas;
    the whole frame gets the _CINE_GRADE cinematic look. Then the static
    gradient scrim and the word-pop caption stream (a concat-demuxer
    slideshow of transparent PNGs from publisher/speech_captions.py)
    overlaid at `band_y` — which the caller centers ON the footage so our
    captions cover any captions burned into the source clip (user,
    2026-09-06). Keeps the speech's own audio. A near-vertical source
    already IS the original framing full-screen, so it cover-crops as
    before.

    Caption timings are relative to the source's t=0, which is also reel
    t=0 here — that alignment is the whole reason this format has no poster
    intro segment.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cta_dur = probe_duration(cta_endcard) if cta_endcard else 0.0
    if cta_endcard and max_seconds - cta_dur < _MIN_BODY_SECONDS:
        log.warning(
            "CTA end-card (%.1fs) leaves under %.0fs of the %.0fs cap — "
            "dropping the CTA.", cta_dur, _MIN_BODY_SECONDS, max_seconds,
        )
        cta_endcard, cta_dur = None, 0.0

    body_max = max_seconds - cta_dur
    src_dur = probe_duration(source_video)
    total_dur = min(src_dur, body_max)
    keep_audio = has_audio(source_video)
    log.info(
        "Speech composite: body %.1fs (src %.1fs, cap %.1fs) + CTA %.1fs; "
        "audio %s", total_dur, src_dur, body_max, cta_dur,
        "kept" if keep_audio else "none -> silent track",
    )

    body_target = (out_path.with_name(out_path.stem + "_bodyseg.mp4")
                   if cta_endcard else out_path)

    # Original-framing layout: punch into the clip by _SPEECH_ZOOM (crop the
    # outer edges, aspect kept — faces read bigger; user 2026-09-06), scale to
    # full width and center it vertically over a blurred darkened cover-crop.
    # The caption band overlays ON the footage (band_y comes from
    # speech_caption_band_y) so our text covers any captions burned into the
    # source clip itself.
    src_w, src_h = probe_dimensions(source_video)
    fg_h = int(round(CANVAS_W * src_h / src_w / 2) * 2) if src_w else 0
    if 0 < fg_h < CANVAS_H - 120:
        fg_y = (CANVAS_H - fg_h) // 2
        # Frosted caption bar: blur + dim the footage strip our captions sit
        # on, so any captions BURNED INTO the source clip (a full-width line
        # our 1-2 word pages could never blanket) are unreadable behind our
        # text — a plain scrim left them legible (verified on the Goggins
        # clip, user 2026-09-06). Reads as a deliberate dark caption band.
        strip_h = max(64, int(round(fg_h * 0.16 / 2)) * 2)
        log.info("Original framing: %dx%d source -> %dx%d at y=%d "
                 "(zoom %.2fx, %dpx frosted bar, captions on footage "
                 "at y=%d).",
                 src_w, src_h, CANVAS_W, fg_h, fg_y, _SPEECH_ZOOM,
                 strip_h, band_y)
        frame_chain = (
            f"[0:v]split=2[bgsrc][fgsrc];"
            f"[bgsrc]{_COVER_FILTER},boxblur=32:2,eq=brightness=-0.22[bg];"
            f"[fgsrc]crop=iw/{_SPEECH_ZOOM}:ih/{_SPEECH_ZOOM},"
            f"scale={CANVAS_W}:-2,setsar=1,split=2[fga][fgb];"
            f"[fgb]crop=iw:{strip_h}:0:(ih-{strip_h})/2,"
            f"boxblur=20:2,colorchannelmixer=rr=0.4:gg=0.4:bb=0.4[fstrip];"
            f"[fga][fstrip]overlay=0:(H-{strip_h})/2[fgs];"
            f"[bg][fgs]overlay=0:{fg_y}[comp];"
        )
    else:
        # Unknown dimensions or an (almost) vertical source: full-frame
        # cover-crop IS the original framing here.
        frame_chain = f"[0:v]{_COVER_FILTER}[comp];"

    video_graph = (
        # [0] source clip, [1] scrim PNG (looped), [2] caption slideshow.
        frame_chain
        + f"[comp]{_CINE_GRADE},fps=30,trim=duration={total_dur:.2f},"
        f"setpts=PTS-STARTPTS[base];"
        f"[1:v]format=rgba[scrim];"
        f"[base][scrim]overlay=0:0:format=auto:shortest=1[lit];"
        f"[2:v]fps=30,format=rgba[cap];"
        # eof_action=pass: when the caption stream ends (it closes with a
        # blank), the footage keeps playing untouched.
        f"[lit][cap]overlay=0:{band_y}:format=auto:eof_action=pass[v]"
    )

    cmd = [
        _resolve_ffmpeg(), "-y", "-loglevel", "warning",
        "-i", str(source_video),
        "-loop", "1", "-i", str(scrim_png),
        "-f", "concat", "-safe", "0", "-i", str(captions_ffconcat),
    ]
    if keep_audio:
        cmd += [
            "-filter_complex",
            video_graph
            + f";[0:a]atrim=duration={total_dur:.2f},asetpts=PTS-STARTPTS[a]",
            "-map", "[v]", "-map", "[a]",
        ]
    else:
        # Input 3: silence — every segment must carry an audio track so the
        # CTA concat can't desync (house rule; a silent speech reel would be
        # a broken source anyway, but never desync over it).
        cmd += [
            "-f", "lavfi", "-t", f"{total_dur:.2f}",
            "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-filter_complex", video_graph,
            "-map", "[v]", "-map", "3:a",
        ]

    cmd += [
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-r", "30", "-g", "30", "-keyint_min", "30",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
        "-movflags", "+faststart",
        "-t", f"{total_dur:.2f}",
        str(body_target),
    ]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        log.error("ffmpeg stderr:\n%s", proc.stderr)
        raise RuntimeError(f"speech composite failed (exit {proc.returncode})")

    if cta_endcard:
        # CTA is pre-normalized to this exact recipe -> clean stream copy.
        _concat_copy([body_target, cta_endcard], out_path)
        try:
            body_target.unlink()
        except OSError:
            pass
    return out_path


def _build_beat_segment(
    card_png: Path,
    clip: Path,
    out_path: Path,
    *,
    seconds: float,
) -> Path:
    """Render ONE beat segment: the beat's clip center-cropped to fill the
    rect, that beat's tweet card overlaid, on the black canvas. Keeps the
    clip's own audio (silent if it has none). Exactly `seconds` long — if
    the clip is shorter it is looped to fill, so every beat holds its full
    on-screen time.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    keep_audio = has_audio(clip)

    crop_filter = _FIT_FILTER
    video_graph = (
        f"[1:v]{crop_filter},fps=30,trim=duration={seconds:.2f},"
        f"setpts=PTS-STARTPTS[clip];"
        f"[0:v][clip]overlay=x={VIDEO_X}:y={VIDEO_Y}:shortest=1[bg];"
        f"[bg][2:v]overlay=x={CARD_X}:y={CARD_Y}:format=auto[v]"
    )

    cmd = [
        _resolve_ffmpeg(), "-y", "-loglevel", "warning",
        "-f", "lavfi", "-t", f"{seconds:.2f}",
        "-i", f"color=c=black:s={CANVAS_W}x{CANVAS_H}:r=30",
        # Loop the clip so a short source still fills the beat's full time.
        "-stream_loop", "-1", "-i", str(clip),
        "-i", str(card_png),
    ]
    # Declare ALL inputs (incl. the silence source) BEFORE any -map/-filter,
    # otherwise ffmpeg rejects the input option ordering. Input index 3 =
    # anullsrc, used only when the clip has no audio of its own.
    if not keep_audio:
        cmd += [
            "-f", "lavfi", "-t", f"{seconds:.2f}",
            "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        ]

    if keep_audio:
        cmd += [
            "-filter_complex",
            video_graph + f";[1:a]atrim=duration={seconds:.2f},asetpts=PTS-STARTPTS[a]",
            "-map", "[v]", "-map", "[a]",
        ]
    else:
        cmd += ["-filter_complex", video_graph, "-map", "[v]", "-map", "3:a"]

    # Every segment MUST carry an audio track (even silence) so the final
    # concat doesn't desync when some beats have audio and others don't.
    cmd += [
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-r", "30", "-g", "30", "-keyint_min", "30",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
        "-t", f"{seconds:.2f}", str(out_path),
    ]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        log.error("ffmpeg stderr:\n%s", proc.stderr)
        raise RuntimeError(f"beat segment failed (exit {proc.returncode})")
    return out_path


def build_multibeat(
    beat_cards: list[Path],
    beat_clips: list[Path],
    out_path: Path,
    *,
    seconds_per_beat: float = 3.5,
    max_seconds: float = 60.0,
) -> Path:
    """Composite a MULTI-BEAT reel: one segment per beat (that beat's clip
    + that beat's tweet card), hard-cut and concatenated in order. This is
    the @evolving.ai look — footage changes on every text reveal.

    `beat_cards` and `beat_clips` are parallel lists (one per beat). Each
    beat holds `seconds_per_beat`, capped so the whole reel <= max_seconds.
    Keeps each clip's own audio across its segment.
    """
    if not beat_cards or len(beat_cards) != len(beat_clips):
        raise ValueError("beat_cards and beat_clips must be same non-zero length")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    n = len(beat_cards)
    per = min(seconds_per_beat, max(1.5, max_seconds / n))
    log.info("Multi-beat: %d beats x %.2fs = %.1fs total", n, per, n * per)

    seg_dir = out_path.parent / "_beats"
    seg_dir.mkdir(parents=True, exist_ok=True)
    segments: list[Path] = []
    for i, (card, clip) in enumerate(zip(beat_cards, beat_clips)):
        seg = seg_dir / f"seg_{i:02d}.mp4"
        log.info("Beat segment %d/%d -> %s", i + 1, n, seg.name)
        _build_beat_segment(card, clip, seg, seconds=per)
        segments.append(seg)

    # Concat via the demuxer (all segments share codec/params, so this is
    # a clean stream copy — fast and frame-accurate).
    concat_list = seg_dir / "concat.txt"
    concat_list.write_text(
        "".join(f"file '{s.as_posix()}'\n" for s in segments),
        encoding="utf-8",
    )
    cmd = [
        _resolve_ffmpeg(), "-y", "-loglevel", "warning",
        "-f", "concat", "-safe", "0", "-i", str(concat_list),
        "-c", "copy", "-movflags", "+faststart",
        str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        log.error("ffmpeg concat stderr:\n%s", proc.stderr)
        raise RuntimeError(f"multi-beat concat failed (exit {proc.returncode})")
    return out_path


def build_still(
    card_png: Path,
    poster_image: Path,
    out_path: Path,
    *,
    duration_seconds: float = 8.0,
) -> Path:
    """Composite a reel from a STILL poster image with a slow Ken Burns
    zoom, plus the tweet card. Used when no source video clip could be
    found (e.g. YouTube search is bot-blocked in CI) — guarantees a reel
    always ships, on-format and with no talking head. Silent by design.

    Same canvas/rect/card geometry as build(), so the result is visually
    consistent with the video version.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    total_dur = max(3.0, duration_seconds)
    frames = int(round(total_dur * 30))

    log.info("Composite (STILL/Ken Burns): poster only -> %.1fs", total_dur)

    # Ken Burns: scale up generously first (so zoompan has pixels to pan
    # into without softening), then a slow 1.0 -> ~1.12 zoom centered.
    # zoompan outputs at the rect size; we feed that straight into overlay.
    kenburns = (
        f"[1:v]scale={VIDEO_W*4}:{VIDEO_H*4}:"
        f"force_original_aspect_ratio=increase,"
        f"crop={VIDEO_W*4}:{VIDEO_H*4},"
        f"zoompan=z='min(zoom+0.0006,1.12)':d={frames}:"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"s={VIDEO_W}x{VIDEO_H}:fps=30,setsar=1[rect]"
    )
    video_graph = (
        kenburns + ";"
        f"[0:v][rect]overlay=x={VIDEO_X}:y={VIDEO_Y}:shortest=1[bg_rect];"
        f"[bg_rect][2:v]overlay=x={CARD_X}:y={CARD_Y}:format=auto[v]"
    )

    cmd = [
        _resolve_ffmpeg(), "-y", "-loglevel", "warning",
        # 0: black canvas, full duration.
        "-f", "lavfi", "-t", f"{total_dur:.2f}",
        "-i", f"color=c=black:s={CANVAS_W}x{CANVAS_H}:r=30",
        # 1: poster (single still; zoompan animates it).
        "-loop", "1", "-t", f"{total_dur:.2f}", "-i", str(poster_image),
        # 2: tweet card PNG.
        "-i", str(card_png),
        "-filter_complex", video_graph,
        "-map", "[v]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-r", "30", "-g", "30", "-keyint_min", "30",
        "-movflags", "+faststart",
        "-an",
        "-t", f"{total_dur:.2f}",
        str(out_path),
    ]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        log.error("ffmpeg stderr:\n%s", proc.stderr)
        raise RuntimeError(
            f"ffmpeg still-composite failed (exit {proc.returncode})"
        )
    return out_path


# ---------------------------------------------------------------------------

def _cli(argv: list[str]) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Composite a tweet-card reel.")
    p.add_argument("--card", required=True, help="Tweet-card PNG")
    p.add_argument("--video", required=True, help="Source video (mp4)")
    p.add_argument("--poster", required=True, help="Poster image (jpg/png)")
    p.add_argument("--out", required=True, help="Output mp4 path")
    p.add_argument("--preview-seconds", type=float, default=1.0)
    p.add_argument("--max-seconds", type=float, default=60.0)
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    out = build(
        Path(args.card), Path(args.video), Path(args.poster), Path(args.out),
        preview_seconds=args.preview_seconds,
        max_seconds=args.max_seconds,
    )
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(_cli(sys.argv[1:]))
