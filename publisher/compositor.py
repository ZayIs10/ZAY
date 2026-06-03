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
