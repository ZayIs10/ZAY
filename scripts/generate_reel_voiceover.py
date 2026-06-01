"""Generate the voiceover MP3 for a reel.

Uses OpenAI's TTS API (model `tts-1-hd`, voice `onyx` — deep serious male).
Generates one MP3 per scene, then concatenates them with silence padding so the
final track aligns with the GSAP timeline in reels/index.html.

Three input modes:
  1. --reel N           -> use the legacy hard-coded LINES_BY_REEL[N] (Reel #1, #2)
  2. --lines-json PATH  -> read a JSON list of {id, start, text} dicts from PATH
  3. --from-sheet ROW   -> read the "Voiceover Lines (JSON)" cell from Google Sheets

Output:
  --reel N           -> reels/assets/audio/reel{N}_voiceover.mp3
  --lines-json/sheet -> reels/assets/audio/<slug>_voiceover.mp3   (--slug controls the name)

Usage:
    python scripts/generate_reel_voiceover.py --reel 1
    python scripts/generate_reel_voiceover.py --lines-json voiceover.json --slug my-reel
    python scripts/generate_reel_voiceover.py --from-sheet 12 --slug my-reel
    python scripts/generate_reel_voiceover.py --reel 1 --voice ash --speed 1.05
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

REPO_ROOT = Path(__file__).resolve().parents[1]
AUDIO_DIR = REPO_ROOT / "reels" / "assets" / "audio"
FFMPEG = REPO_ROOT / "node_modules" / "@ffmpeg-installer" / "win32-x64" / "ffmpeg.exe"
if not FFMPEG.exists():
    FFMPEG = Path("ffmpeg")  # fallback to PATH

# Each entry: (scene id, start time in seconds, text). Scene durations are
# implicit from the next start time (or 30.0 for the last). The TTS clip is
# placed at `start`, padded with leading silence; remaining time becomes
# trailing silence at the end of the master track.
#
# LINES_BY_REEL keys = reel number. Add a new entry per reel as the series
# grows. Scene timing must align with the GSAP timeline in reels/index.html
# at the time of rendering — keep them in sync when editing one or the other.

LINES_BY_REEL = {
    # Reel #1 — Faceless Montage Hook (6 accounts, 30M+ followers)
    1: [
        ("s1", 0.20,
         "Six Instagram accounts. Over thirty million followers combined. "
         "None of them ever showed their face."),
        ("s2", 8.30, "So how did they do it?"),
        ("s3", 12.55,
         "Every single one of them does the same three things. "
         "One niche. One visual format. Posted every day. "
         "No experimenting. No personality. Just a system."),
        ("s4", 23.30,
         "But there are four more tricks they don't talk about."),
        ("s5", 27.20, "Comment NEXT for the four secrets."),
    ],
    # Reel #2 — The Algorithm Decoded (sends/watch/saves > likes)
    # Each VO line lands ~0.5s after its scene starts so the on-screen text
    # is visible BEFORE the voice starts (algorithm rule #2 in
    # docs/reel_creation.md).
    2: [
        ("s1", 0.50,
         "Instagram doesn't count likes."),
        ("s2", 3.50,
         "The strongest signal is sends per reach. "
         "How many people DM your post. Likes are the weakest signal."),
        ("s3", 10.50,
         "Shares first. Watch time second. Saves third. "
         "They engineer DMs, not likes."),
        ("s4", 20.50,
         "Hit one percent sends per reach, and Instagram pushes your reel "
         "to non-followers. That's how zero-follower accounts go viral."),
        ("s5", 27.50,
         "Comment NEXT for the four content types."),
    ],
    # Reel #5 — 3 Countries Where You'd Be Rich Tomorrow (geographic arbitrage)
    # 5-beat generic-cinematic template. VO lands ~0.5s after each beat starts
    # so on-screen text reveals first (algorithm rule #2).
    5: [
        ("s1", 0.50,
         "Your sixty K salary is worthless at home. "
         "In these three countries, it's a fortune."),
        ("s2", 3.50,
         "Dubai. Zero income tax. "
         "Sixty K lives like one hundred twenty."),
        ("s3", 10.50,
         "Portugal. The NHR visa. "
         "Ten years of almost no tax. Legally."),
        ("s4", 20.50,
         "Bali. Two thousand a month "
         "buys the life everyone posts about."),
        ("s5", 27.50,
         "Follow for the move-abroad playbook."),
    ],
    # Reel #4 — The 4 Secrets (payoff to Reel 1's "comment NEXT" cliffhanger)
    4: [
        ("s1", 0.30,
         "You asked for the four secrets. Here they are."),
        ("s2", 4.30,
         "One. They never show their face. No burnout. No risk."),
        ("s3", 10.30,
         "Two. They don't write content. They write one hook, fifty variations."),
        ("s4", 16.30,
         "Three. Every caption ends the same way. "
         "Comment a word. The DM does the selling."),
        ("s5", 22.30,
         "Four. The clips? AI. The voice? AI. Even the captions."),
        ("s6", 27.30,
         "Follow for the playbook."),
    ],
}
TOTAL_DURATION = 30.0


def _tts(client: OpenAI, text: str, out_path: Path, voice: str, speed: float) -> None:
    """Render one line of TTS to MP3."""
    print(f"  -> TTS: {out_path.name}  ({len(text)} chars)")
    resp = client.audio.speech.create(
        model="tts-1-hd",
        voice=voice,
        input=text,
        speed=speed,
        response_format="mp3",
    )
    resp.write_to_file(str(out_path))


def _ffmpeg(args: list[str]) -> None:
    cmd = [str(FFMPEG), "-y", "-loglevel", "warning", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write("STDOUT:\n" + (proc.stdout or "") + "\n")
        sys.stderr.write("STDERR:\n" + (proc.stderr or "") + "\n")
        raise RuntimeError(f"ffmpeg failed: {' '.join(cmd)}")


def _probe_duration(path: Path) -> float:
    """Return MP3 duration in seconds via ffprobe."""
    ffprobe = REPO_ROOT / "node_modules" / "@ffprobe-installer" / "win32-x64" / "ffprobe.exe"
    if not ffprobe.exists():
        ffprobe = Path("ffprobe")
    proc = subprocess.run(
        [str(ffprobe), "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(proc.stdout.strip())


def _lines_from_json_payload(payload) -> list[tuple[str, float, str]]:
    """Coerce a JSON list of {id, start, text} dicts into the tuple form."""
    if not isinstance(payload, list):
        sys.exit("Voiceover lines JSON must be a list of objects")
    out: list[tuple[str, float, str]] = []
    for i, item in enumerate(payload):
        scene_id = str(item.get("id") or f"s{i+1}")
        start = float(item.get("start", 0.0))
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        out.append((scene_id, start, text))
    if not out:
        sys.exit("No usable lines in voiceover JSON")
    return out


def _lines_from_sheet_row(row_index: int) -> list[tuple[str, float, str]]:
    """Read the 'Voiceover Lines (JSON)' cell from Google Sheets."""
    repo_root = REPO_ROOT
    sys.path.insert(0, str(repo_root / "publisher"))
    from post_generator import SheetsReader  # type: ignore

    config = {
        "google_sheets": {
            "credentials_file": "google_service_account.json",
            "spreadsheet_id": os.getenv("GOOGLE_SHEET_ID", ""),
            "sheet_name": os.getenv("GOOGLE_SHEET_REELS_NAME", "Reels"),
        }
    }
    reader = SheetsReader(config)
    all_values = reader.ws.get_all_values()
    if row_index < 2 or row_index > len(all_values):
        sys.exit(f"Row {row_index} not found in sheet")
    headers = all_values[0]
    raw = all_values[row_index - 1]
    row = {headers[j]: raw[j] if j < len(raw) else "" for j in range(len(headers))}

    cell = row.get("Voiceover Lines (JSON)", "").strip()
    if not cell:
        sys.exit(f"Row {row_index} has no 'Voiceover Lines (JSON)' content")
    return _lines_from_json_payload(json.loads(cell))


def main() -> None:
    parser = argparse.ArgumentParser()
    src = parser.add_mutually_exclusive_group()
    src.add_argument(
        "--reel", type=int, default=None,
        choices=sorted(LINES_BY_REEL.keys()),
        help="Use a hard-coded LINES_BY_REEL entry (legacy, default if no other source).",
    )
    src.add_argument(
        "--lines-json", type=Path, default=None,
        help="Path to a JSON file: list of {id, start, text} dicts.",
    )
    src.add_argument(
        "--from-sheet", type=int, default=None,
        help="Google Sheets row index (1-based, includes header).",
    )
    parser.add_argument(
        "--slug", default=None,
        help="Slug for the output MP3 (default: reel{N} for --reel, else 'reel').",
    )
    parser.add_argument(
        "--voice", default="onyx",
        help="OpenAI TTS voice (onyx, ash, ballad, coral, echo, "
             "fable, alloy, sage, shimmer, verse).",
    )
    parser.add_argument("--speed", type=float, default=1.0)
    args = parser.parse_args()

    if args.lines_json:
        payload = json.loads(args.lines_json.read_text(encoding="utf-8"))
        lines = _lines_from_json_payload(payload)
        slug = args.slug or args.lines_json.stem
        source_label = f"JSON({args.lines_json.name})"
    elif args.from_sheet:
        lines = _lines_from_sheet_row(args.from_sheet)
        slug = args.slug or f"sheet-row-{args.from_sheet}"
        source_label = f"Sheet row {args.from_sheet}"
    else:
        reel_n = args.reel or 1
        lines = LINES_BY_REEL[reel_n]
        slug = args.slug or f"reel{reel_n}"
        source_label = f"Reel #{reel_n}"

    load_dotenv(REPO_ROOT / ".env")
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        sys.exit("OPENAI_API_KEY not set in .env")

    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    client = OpenAI(api_key=api_key)

    # 1. Generate one MP3 per line
    print(
        f"{source_label}: generating {len(lines)} TTS clips "
        f"with voice={args.voice} speed={args.speed}..."
    )
    clip_paths: list[tuple[str, float, Path]] = []
    for scene_id, start, text in lines:
        out = AUDIO_DIR / f"vo_{scene_id}.mp3"
        _tts(client, text, out, args.voice, args.speed)
        clip_paths.append((scene_id, start, out))

    # 2. Pad each clip with leading silence so it lands at its `start` time;
    #    re-encode as a known WAV so concat is sample-accurate.
    print("Aligning clips on the 30-second timeline...")
    aligned_wavs: list[Path] = []
    cursor = 0.0
    for i, (scene_id, start, mp3) in enumerate(clip_paths):
        gap = max(0.0, start - cursor)
        wav = AUDIO_DIR / f"_aligned_{scene_id}.wav"
        next_start = (clip_paths[i + 1][1]
                      if i + 1 < len(clip_paths) else TOTAL_DURATION)
        slot_len = next_start - cursor
        # adelay introduces leading silence; apad (no args) pads infinitely
        # with silence; -t truncates to the exact slot length.
        delay_ms = int(round(gap * 1000))
        _ffmpeg([
            "-i", str(mp3),
            "-af", f"adelay={delay_ms}|{delay_ms},apad",
            "-t", f"{slot_len:.3f}",
            "-ar", "44100", "-ac", "2",
            str(wav),
        ])
        aligned_wavs.append(wav)
        cursor = next_start

    # 3. Concat all aligned wavs into a single mp3 master track
    concat_list = AUDIO_DIR / "_concat.txt"
    concat_list.write_text(
        "\n".join(f"file '{w.as_posix()}'" for w in aligned_wavs),
        encoding="utf-8",
    )
    out_mp3 = AUDIO_DIR / f"{slug}_voiceover.mp3"
    _ffmpeg([
        "-f", "concat", "-safe", "0",
        "-i", str(concat_list),
        "-c:a", "libmp3lame", "-b:a", "192k",
        "-t", f"{TOTAL_DURATION:.3f}",
        str(out_mp3),
    ])

    # 4. Cleanup intermediates
    for w in aligned_wavs:
        w.unlink(missing_ok=True)
    concat_list.unlink(missing_ok=True)

    final_dur = _probe_duration(out_mp3)
    size_kb = out_mp3.stat().st_size / 1024
    print(f"\nOK Wrote {out_mp3.relative_to(REPO_ROOT)}  ({final_dur:.2f}s, {size_kb:.0f} KB)")


if __name__ == "__main__":
    main()
