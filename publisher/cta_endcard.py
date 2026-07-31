"""Supply the "comment SEND" call-to-action end-card for reels.

Why this exists: the Post Caption's `Comment "Send"` line was not
converting (people don't read captions), so as of 2026-07-31 the CTA
moved INTO the reel — the last 5 seconds now SHOW the ask instead of
merely writing it.

The card itself is a HyperFrames composition, hand-built and rendered
locally, then committed as a fixed asset:

    source      reels/compositions/cta_send.html   (edit this)
    re-render   cd reels && npx hyperframes render \\
                    -c compositions/cta_send.html -o renders/cta_send.mp4
    asset       assets/cta/cta_send.mp4            (committed, force-added
                                                    past the *.mp4 ignore)

It shows an Instagram DM thread on pure black: the word "Send" is typed
into the message bar letter by letter, the neon-green send button is
tapped, the bubble springs up into the thread, a typing indicator
bounces, and a reply slides in carrying the YouTube link card.

This module does NOT generate the animation — it only NORMALIZES the
committed asset to the exact encode recipe compositor.py uses for every
other segment (libx264 fast crf20 yuv420p, 30fps g=30, plus a silent aac
44.1k stereo track, which the HyperFrames render has no reason to carry).
That lets the reel builder concatenate it as a pure stream copy.

Rendering is a local, one-off, human-reviewed step precisely because the
CTA is brand-facing — CI never re-renders it, so what you approved is
exactly what ships.

Public API:
    endcard_for_reel(work_dir) -> Path | None      # never raises
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from publisher.media_consumer import _resolve_ffmpeg  # noqa: E402

log = logging.getLogger("cta_endcard")

SOURCE_ASSET = _REPO_ROOT / "assets" / "cta" / "cta_send.mp4"
COMPOSITION = _REPO_ROOT / "reels" / "compositions" / "cta_send.html"

# Must stay identical to compositor.py's segment recipe or the concat
# stops being a clean stream copy.
_V_OPTS = [
    "-c:v", "libx264", "-preset", "fast", "-crf", "20",
    "-pix_fmt", "yuv420p",
    "-r", "30", "-g", "30", "-keyint_min", "30",
]
_A_OPTS = ["-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2"]


def _disabled() -> bool:
    return os.getenv("DISABLE_CTA_ENDCARD", "").strip().lower() in (
        "1", "true", "yes")


def normalize(src: Path, out_path: Path) -> Path:
    """Re-encode `src` to the compositor's segment recipe, adding a silent
    stereo track (every concatenated segment must carry audio or the join
    desyncs). Returns `out_path`."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        _resolve_ffmpeg(), "-y", "-loglevel", "warning",
        "-i", str(src),
        "-f", "lavfi", "-i",
        "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-map", "0:v:0", "-map", "1:a:0", "-shortest",
        *_V_OPTS, *_A_OPTS,
        "-movflags", "+faststart",
        str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        log.error("ffmpeg stderr:\n%s", proc.stderr)
        raise RuntimeError(f"CTA normalize failed (exit {proc.returncode})")
    return out_path


def endcard_for_reel(work_dir: Path) -> Path | None:
    """Return a concat-ready CTA end-card, or None if it is unavailable or
    switched off. NEVER raises — a missing CTA must not fail a render."""
    if _disabled():
        log.info("CTA end-card: disabled via DISABLE_CTA_ENDCARD.")
        return None
    try:
        if not SOURCE_ASSET.exists():
            log.warning(
                "CTA end-card: %s is missing — building without it. "
                "Re-render it from %s.",
                SOURCE_ASSET.relative_to(_REPO_ROOT),
                COMPOSITION.relative_to(_REPO_ROOT),
            )
            return None
        out = work_dir / "cta_endcard.mp4"
        normalize(SOURCE_ASSET, out)
        log.info("CTA end-card: comment-SEND closer ready (%s)", out.name)
        return out
    except Exception as exc:  # noqa: BLE001 — CTA must never kill a build
        log.warning("CTA end-card: unavailable (%s) — building without it.",
                    exc)
        return None


# ---------------------------------------------------------------------------

def _cli(argv: list[str]) -> int:
    import argparse

    p = argparse.ArgumentParser(
        description="Normalize the committed CTA end-card asset.")
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print(f"wrote {normalize(SOURCE_ASSET, Path(args.out))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
