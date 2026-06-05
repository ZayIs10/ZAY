"""ffmpeg compositor for the @execute-style tweet-card reel.

Stacks three layers onto a 1080x1920 black canvas:
  1. Black background (full duration)
  2. Lower rect at (60, 820), 960x900: a 1-second poster image
     followed by the source video, both scaled+center-cropped.
  3. Tweet-card PNG at (40, 60), shown the full duration (static).

Audio: if the source video HAS an audio track, the reel keeps it —
preceded by `preview_seconds` of silence so it lines up with the poster
intro. If the source has no audio, the reel is silent (-an). Total
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


def build(
    card_png: Path,
    source_video: Path,
    poster_image: Path,
    out_path: Path,
    *,
    preview_seconds: float = 1.0,
    max_seconds: float = 60.0,
) -> Path:
    """Composite the reel and write `out_path`. Returns the output path."""
    out_path.parent.mkdir(parents=True, exist_ok=True)

    src_dur = probe_duration(source_video)
    total_dur = min(preview_seconds + src_dur, max_seconds)
    # If we hit the cap, the source plays for (max - preview) seconds.
    src_play_dur = max(0.5, total_dur - preview_seconds)

    log.info(
        "Composite: poster %.1fs + source %.1fs (cap %.1f) -> total %.1fs",
        preview_seconds, src_play_dur, max_seconds, total_dur,
    )

    crop_filter = (
        f"scale={VIDEO_W}:{VIDEO_H}:force_original_aspect_ratio=increase,"
        f"crop={VIDEO_W}:{VIDEO_H},setsar=1"
    )

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
    if keep_audio:
        cmd += ["-c:a", "aac", "-b:a", "128k"]
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

    crop_filter = (
        f"scale={VIDEO_W}:{VIDEO_H}:force_original_aspect_ratio=increase,"
        f"crop={VIDEO_W}:{VIDEO_H},setsar=1"
    )
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
