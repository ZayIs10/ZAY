"""Build + publish the next 'Ready' Reel from the Google Sheet.

Pipeline:
  1. Find the next row with Status='Ready' (or a specific row via --row).
  2. Generate the voiceover MP3 from the row's 'Voiceover Lines (JSON)' cell.
  3. Render the reel (Montage Hook or 5-Beat) via reel_generator.
  4. Mux the voiceover into the silent rendered MP4 (HyperFrames doesn't mux).
  5. Verify with ffprobe (duration ~30s, 1080x1920, h264 video + aac audio).
  6. Upload + post to Instagram via publish_reel.
  7. Mark the row Status='Published' with the permalink.

Usage:
    python scripts/build_and_publish_reel.py
    python scripts/build_and_publish_reel.py --row 12          # specific row
    python scripts/build_and_publish_reel.py --no-publish      # skip Instagram
    python scripts/build_and_publish_reel.py --no-mux          # skip ffmpeg mux

    # Render-to-review flow (no Instagram post): pick the best Draft, render it,
    # upload to Google Drive, write the link back to the Sheet.
    python scripts/build_and_publish_reel.py --pick-best-draft --to-drive
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
RENDERS_DIR = REPO_ROOT / "renders"
AUDIO_DIR = REPO_ROOT / "reels" / "assets" / "audio"
ASSETS_REELS_DIR = REPO_ROOT / "assets" / "reels"

sys.path.insert(0, str(REPO_ROOT / "publisher"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("build_and_publish_reel")


# Resolve ffmpeg / ffprobe (installer first, then PATH)
def _resolve_binary(name: str) -> str:
    candidate = (
        REPO_ROOT / "node_modules" / f"@{name}-installer" / "win32-x64" /
        f"{name}.exe"
    )
    return str(candidate) if candidate.exists() else name


FFMPEG = _resolve_binary("ffmpeg")
FFPROBE = _resolve_binary("ffprobe")


def find_render_for_slug(slug: str) -> Path | None:
    """reel_generator writes to assets/reels/<slug>.mp4 by default."""
    p = ASSETS_REELS_DIR / f"{slug}.mp4"
    if p.exists():
        return p
    # Some configs write to renders/reels_<timestamp>.mp4 instead — fall back to
    # the most recent reels_* file if the named one isn't there.
    if RENDERS_DIR.exists():
        candidates = sorted(RENDERS_DIR.glob("reels_*.mp4"))
        if candidates:
            return candidates[-1]
    return None


def mux_audio(silent_mp4: Path, voiceover: Path, output: Path) -> None:
    """Mux the voiceover into the silent rendered MP4. HyperFrames doesn't do
    this on its own — without this step the posted reel has zero audio.
    """
    cmd = [
        FFMPEG, "-y", "-loglevel", "warning",
        "-i", str(silent_mp4),
        "-i", str(voiceover),
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(output),
    ]
    log.info("Muxing audio: %s", " ".join(cmd))
    proc = subprocess.run(cmd)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg mux failed (exit {proc.returncode})")


def verify_mp4(path: Path) -> None:
    """Confirm the muxed MP4 has video + audio + correct dimensions."""
    cmd = [
        FFPROBE, "-v", "error",
        "-show_entries", "format=duration",
        "-show_entries", "stream=codec_type,codec_name,width,height",
        "-of", "default", str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {proc.stderr}")
    out = proc.stdout
    log.info("ffprobe output:\n%s", out)
    has_video = "codec_type=video" in out and "codec_name=h264" in out
    has_audio = "codec_type=audio" in out
    if not has_video:
        raise RuntimeError("Final MP4 missing h264 video stream")
    if not has_audio:
        raise RuntimeError("Final MP4 missing audio stream — mux failed silently")


def _find_best_draft(reader) -> int | None:
    """Return the 1-based index of the 'Draft' row with the highest 'Reel Fit'
    score. Rows with no score count as 0; if nothing has a score the first
    Draft row wins. Returns None when there are no Draft rows at all.
    """
    values = reader.ws.get_all_values()
    if len(values) < 2:
        return None
    headers = values[0]
    status_c = headers.index("Status") if "Status" in headers else -1
    fit_c = headers.index("Reel Fit") if "Reel Fit" in headers else -1
    best_idx: int | None = None
    best_score = float("-inf")
    for i, raw in enumerate(values[1:], start=2):
        status = raw[status_c].strip().lower() if 0 <= status_c < len(raw) else ""
        if status != "draft":
            continue
        score = 0.0
        if 0 <= fit_c < len(raw) and raw[fit_c].strip():
            try:
                score = float(raw[fit_c])
            except ValueError:
                score = 0.0
        if score > best_score:
            best_score = score
            best_idx = i
    return best_idx


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build and publish the next Ready reel from Google Sheets.",
    )
    parser.add_argument("--row", type=int, default=None,
                        help="Specific Sheet row index to publish.")
    parser.add_argument("--no-publish", action="store_true",
                        help="Stop after the muxed MP4 is built. Don't post to IG.")
    parser.add_argument("--no-mux", action="store_true",
                        help="Skip the audio mux step (debug only).")
    parser.add_argument("--quality", default="standard",
                        choices=["draft", "standard", "high"])
    parser.add_argument("--pexels", action="store_true",
                        help="Use copyright-safe Pexels stock photos as backgrounds "
                             "instead of the local DALL-E images.")
    parser.add_argument("--pexels-video", action="store_true",
                        help="Use Pexels portrait videos as moving backgrounds "
                             "(one pre-cut clip per beat).")
    parser.add_argument("--pexels-query", default=None,
                        help="Override the Pexels search query (default: auto from "
                             "the row's Topic + hook lines).")
    parser.add_argument("--pick-best-draft", action="store_true",
                        help="Render the 'Draft' row with the highest Reel Fit "
                             "score instead of the next 'Ready' row.")
    parser.add_argument("--to-drive", action="store_true",
                        help="After rendering, upload the MP4 to Google Drive, "
                             "write the link to the Sheet, and stop. No IG post.")
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")

    # 1. Find the row
    from reel_generator import load_sheet_row, spec_from_sheet_row  # type: ignore

    if args.pick_best_draft and args.row is None:
        _, _reader0 = load_sheet_row(row_index=1)  # row 1 = header -> (None, reader)
        best = _find_best_draft(_reader0)
        if best is None:
            log.error("No 'Draft' rows in the Reels tab. Run the research "
                      "workflow first.")
            return 1
        log.info("Auto-picked highest reel-fit Draft -> row %d", best)
        args.row = best

    row, reader = load_sheet_row(row_index=args.row)
    if not row:
        log.error("No Ready row found. Add a draft via scripts/research_topic.py "
                  "and flip Status to Ready.")
        return 1

    row_index = row.get("_row_index")
    topic = row.get("Topic", "").strip() or "Untitled"
    log.info("Building reel for row %s: %s", row_index, topic)

    spec = spec_from_sheet_row(row)
    spec.quality = args.quality
    log.info("Template: %s  |  Slug: %s", spec.template, spec.slug)

    # 2. Voiceover
    voiceover_path = AUDIO_DIR / f"{spec.slug}_voiceover.mp3"
    log.info("Generating voiceover -> %s", voiceover_path)
    vo_cmd = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "generate_reel_voiceover.py"),
        "--from-sheet", str(row_index),
        "--slug", spec.slug,
    ]
    proc = subprocess.run(vo_cmd, cwd=REPO_ROOT)
    if proc.returncode != 0:
        log.error("Voiceover generation failed (exit %d)", proc.returncode)
        return 2
    if not voiceover_path.exists():
        log.error("Voiceover script ran but %s is missing", voiceover_path)
        return 2

    # 3. Render
    log.info("Rendering reel...")
    render_cmd = [
        sys.executable,
        str(REPO_ROOT / "publisher" / "reel_generator.py"),
        "--from-sheet", str(row_index),
        "--quality", args.quality,
    ]
    if args.pexels:
        render_cmd.append("--pexels")
    if args.pexels_video:
        render_cmd.append("--pexels-video")
    if (args.pexels or args.pexels_video) and args.pexels_query:
        render_cmd.extend(["--pexels-query", args.pexels_query])
    proc = subprocess.run(render_cmd, cwd=REPO_ROOT)
    if proc.returncode != 0:
        log.error("Render failed (exit %d)", proc.returncode)
        return 3

    silent_mp4 = find_render_for_slug(spec.slug)
    if not silent_mp4 or not silent_mp4.exists():
        log.error("Could not locate rendered MP4 for slug %s", spec.slug)
        return 3
    log.info("Rendered MP4: %s (%.1f MB)",
             silent_mp4, silent_mp4.stat().st_size / 1e6)

    # 4. Mux audio
    final_mp4 = RENDERS_DIR / f"{spec.slug}.mp4"
    RENDERS_DIR.mkdir(parents=True, exist_ok=True)
    if args.no_mux:
        log.warning("Skipping mux (--no-mux). Reel will have NO audio on Instagram.")
        final_mp4 = silent_mp4
    else:
        mux_audio(silent_mp4, voiceover_path, final_mp4)
        verify_mp4(final_mp4)
        log.info("Muxed MP4: %s (%.1f MB)",
                 final_mp4, final_mp4.stat().st_size / 1e6)

    # 5a. Render-to-review: upload to Drive, write the link back, no IG post.
    if args.to_drive:
        sys.path.insert(0, str(REPO_ROOT / "publisher"))
        from publish_reel import upload_to_drive  # type: ignore
        log.info("Uploading to Google Drive (skipping Instagram)...")
        dl_url = upload_to_drive(final_mp4)
        file_id = dl_url.split("id=")[-1]
        view_url = f"https://drive.google.com/file/d/{file_id}/view"
        caption = (row.get("Post Caption") or "").strip() or topic
        if row_index:
            try:
                reader.ws.update_cell(
                    row_index, reader._col_index("Reel MP4 URL"), view_url)
                reader.ws.update_cell(
                    row_index, reader._col_index("Status"), "Rendered - Review")
                log.info("Row %d: wrote Drive link, Status -> Rendered - Review",
                         row_index)
            except Exception as exc:
                log.warning("Couldn't update the Sheet row: %s", exc)
        print("\n=== RENDERED - REVIEW ON YOUR PHONE ===")
        print(f"Drive video : {view_url}")
        print(f"Sheet row   : {row_index}")
        print("\n--- CAPTION (copy into Instagram) ---")
        print(caption)
        print("--- end caption ---\n")
        return 0

    # 5. Publish
    if args.no_publish:
        log.info("--no-publish: stopping. Final MP4: %s", final_mp4)
        return 0

    caption = (row.get("Post Caption") or row.get("post_caption") or "").strip()
    if not caption:
        # Fall back to a sane minimum so the post isn't blank
        caption = topic + "\n\nFollow @genz_capitalbusiness for more."

    publish_cmd = [
        sys.executable,
        str(REPO_ROOT / "publisher" / "publish_reel.py"),
        "--video", str(final_mp4),
        "--caption", caption,
    ]
    log.info("Publishing to Instagram: %s", " ".join(publish_cmd))
    proc = subprocess.run(publish_cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    print(proc.stdout)
    if proc.returncode != 0:
        log.error("Publish failed (exit %d): %s", proc.returncode, proc.stderr)
        return 4

    # 6. Parse permalink + media_id from publish_reel stdout
    permalink = ""
    media_id = ""
    for line in proc.stdout.splitlines():
        if line.startswith("Permalink:"):
            permalink = line.split(":", 1)[1].strip()
        if line.startswith("Media ID:"):
            media_id = line.split(":", 1)[1].strip()

    # 7. Mark Sheet row Published
    if row_index and (permalink or media_id):
        try:
            reader.mark_published(row_index, permalink or "", media_id or "")
            log.info("Marked row %d Published", row_index)
        except Exception as exc:
            log.warning("Couldn't mark row Published: %s", exc)

    log.info("Done. %s", permalink or "(check Instagram for permalink)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
