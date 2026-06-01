"""
Gen Z Capital — Reel Generator (HyperFrames-driven)

Reads a "Ready" topic from Google Sheets, builds a 30-second 9:16 reel using
the GenZ brand template (5-beat structure from docs/reel_creation.md), and
renders it to MP4 via HyperFrames + Chrome Headless + FFmpeg.

Usage:
    # Render with hard-coded sample data (smoke test, no Sheets, no APIs):
    python publisher/reel_generator.py --sample

    # Render the next "Ready" row from Google Sheets (full pipeline):
    python publisher/reel_generator.py

    # Render with a JSON spec (skip GPT, skip Sheets):
    python publisher/reel_generator.py --spec path/to/spec.json

The reel HTML lives at `reels/index.html`. This script overwrites it on every
run, then invokes `npm run reels:render` to produce `assets/reels/<slug>.mp4`.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

# Repo paths
REPO_ROOT = Path(__file__).resolve().parents[1]
REELS_DIR = REPO_ROOT / "reels"
ASSETS_REELS_DIR = REPO_ROOT / "assets" / "reels"
GENERATED_IMAGES_DIR = REPO_ROOT / "assets" / "images" / "generated"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("reel_generator")


# ---------------------------------------------------------------------------
# Reel content model — one ReelSpec describes one reel.
# ---------------------------------------------------------------------------

@dataclass
class ReelSpec:
    """Everything the template needs to render a single reel."""
    # slug used for output filename
    slug: str

    # Which template to render: "five_beat" (default, generic 5-beat narrative) or
    # "montage_hook" (Reel #1 clone — 6 fixed account screenshots + question/reveal).
    template: str = "five_beat"

    # Beat 1 — Hook (0–3s) — five_beat only
    hook_line_1: str = ""
    hook_line_2: str = ""   # rendered in neon green
    hook_line_3: str = ""
    hook_subline: str = ""  # gray, "...AND HERE'S WHY."

    # Beat 2 — Problem (3–10s) — five_beat only
    problem_a: str = ""     # first message
    problem_b: str = ""     # crossfades to this
    problem_c: str = ""     # optional third line (sub)

    # Beat 3 — Insight (10–20s) — five_beat only
    insight_1: str = ""
    insight_2: str = ""     # neon green key number/word
    insight_3: str = ""

    # Beat 4 — Proof (20–27s) — used by both templates
    proof_number: str = ""  # e.g. "$1.4M" or "37%"
    proof_label: str = ""
    proof_sublabel: str = "" # optional small line under the label

    # Beat 5 — CTA (27–30s) — five_beat only — defaults match brand
    cta_save: str = "SAVE THIS."
    cta_follow: str = "FOLLOW @GENZ_CAPITALBUSINESS"

    # Image paths (relative to reels/index.html, so usually "../assets/images/generated/<file>")
    # five_beat only — Montage Hook uses fixed brand screenshots.
    img_hook: str = ""
    img_problem: str = ""
    img_insight: str = ""
    img_cta: str = ""

    # Optional video clip paths (per beat, relative to reels/index.html, e.g.
    # "assets/clips/<slug>_b1.mp4"). When set, the corresponding beat renders
    # a <video> background instead of <img>. Beat 4 (proof) stays solid black.
    vid_hook: str = ""
    vid_problem: str = ""
    vid_insight: str = ""
    vid_cta: str = ""

    # Per-beat Pexels search queries (hook, problem, insight, cta) — used with
    # --pexels-video so every beat gets a clip matched to its own content.
    # Empty list = fall back to queries derived from each beat's text.
    pexels_queries: list[str] = field(default_factory=list)

    # ---- Montage Hook fields (template == "montage_hook") --------------------
    # Scene 2 (the question) — defaults to Reel #1 wording.
    question_line_a: str = "SO HOW DID"
    question_line_b: str = "THEY DO IT?"
    # Scene 3 (the partial reveal) — three short lines, middle is neon.
    reveal_line_1: str = "ONE NICHE."
    reveal_line_2: str = "ONE FORMAT."
    reveal_line_3: str = "POSTED DAILY."
    # Scene 4 (the cliffhanger) — middle line is "<count> <label>", e.g. "4 MORE TRICKS".
    cliffhanger_count: str = "4 MORE"
    cliffhanger_label: str = "TRICKS"
    # Scene 5 (the CTA) — "COMMENT '<word>' / FOR THE <count> <label>".
    cta_comment_word: str = "NEXT"

    # Optional audio — paths relative to reels/index.html. Empty string = no audio track.
    voiceover_src: str = ""    # e.g. "assets/<slug>_vo.mp3"
    music_src: str = ""        # e.g. "assets/<slug>_music.mp3"
    voiceover_volume: float = 1.0
    music_volume: float = 0.20  # rule #5 in reel_creation.md: 20% under voiceover

    # Render settings
    fps: int = 30
    quality: str = "standard"  # draft | standard | high


# ---------------------------------------------------------------------------
# HTML builder — constructs reels/index.html from a ReelSpec.
# ---------------------------------------------------------------------------

def _esc(s: str) -> str:
    """HTML-escape user-supplied text content."""
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


REELS_ARCHIVE_DIR = REPO_ROOT / "reels" / "archive"


def build_montage_hook_html(spec: ReelSpec) -> str:
    """Build the Montage Hook composition by templating off Reel #1's archived HTML.

    The 6 account screenshots and follower counts stay fixed (they're the brand's
    social-proof set). The editable parts are the reveal lines, cliffhanger text,
    CTA wording, and the audio src. We do narrow string replacements so any future
    edit to the archived file (animation timing, vignette, etc.) is preserved.
    """
    base = (REELS_ARCHIVE_DIR / "index_reel1.html").read_text(encoding="utf-8")

    # Scene 3 — the partial reveal (3 lines, middle is neon).
    base = base.replace(
        '<div class="reveal-line a">ONE NICHE.</div>',
        f'<div class="reveal-line a">{_esc(spec.reveal_line_1)}</div>',
    )
    base = base.replace(
        '<div class="reveal-line b neon">ONE FORMAT.</div>',
        f'<div class="reveal-line b neon">{_esc(spec.reveal_line_2)}</div>',
    )
    base = base.replace(
        '<div class="reveal-line c">POSTED DAILY.</div>',
        f'<div class="reveal-line c">{_esc(spec.reveal_line_3)}</div>',
    )

    # Scene 2 — the question (defaults match Reel #1, but topic can override).
    base = base.replace(
        '<div class="q-line a">SO HOW DID</div>',
        f'<div class="q-line a">{_esc(spec.question_line_a)}</div>',
    )
    base = base.replace(
        '<div class="q-line b neon">THEY DO IT?</div>',
        f'<div class="q-line b neon">{_esc(spec.question_line_b)}</div>',
    )

    # Scene 4 — the cliffhanger.
    base = base.replace(
        '<div class="cliff-line b neon">4 MORE TRICKS</div>',
        f'<div class="cliff-line b neon">'
        f'{_esc(spec.cliffhanger_count)} {_esc(spec.cliffhanger_label)}</div>',
    )

    # Scene 5 — the CTA. The "COMMENT 'NEXT'" wording and the "FOR THE 4 SECRETS"
    # line both reference the same comment word + cliffhanger count.
    base = base.replace(
        "<div class=\"cta-comment\">COMMENT 'NEXT'</div>",
        f"<div class=\"cta-comment\">COMMENT '{_esc(spec.cta_comment_word)}'</div>",
    )
    base = base.replace(
        '<div class="cta-secrets neon">FOR THE 4 SECRETS</div>',
        f'<div class="cta-secrets neon">FOR THE {_esc(spec.cliffhanger_count)} '
        f'{_esc(spec.cliffhanger_label.replace("TRICKS", "SECRETS"))}</div>',
    )

    # Audio src — Reel #1 hardcoded reel1_voiceover.mp3; swap to the per-slug file.
    if spec.voiceover_src:
        base = base.replace(
            'src="assets/audio/reel1_voiceover.mp3"',
            f'src="{_esc(spec.voiceover_src)}"',
        )

    return base


def build_reel_html(spec: ReelSpec) -> str:
    """Dispatch to the correct template builder based on spec.template."""
    if spec.template == "montage_hook":
        return build_montage_hook_html(spec)
    return build_five_beat_html(spec)


def _bg_media(video_src: str, image_src: str) -> str:
    """Render a beat background — <video> if a clip path is set, else <img>.

    `preload="auto"` + per-scene clip files avoid the HyperFrames
    'video metadata not ready' timeout (see memory: Reels Pipeline pitfall 2).
    The video is muted; voiceover is muxed in after render.
    """
    if video_src:
        return (
            f'<video class="bg-video" src="{_esc(video_src)}" '
            f'autoplay muted playsinline preload="auto"></video>'
        )
    return f'<div class="bg-image full"><img src="{_esc(image_src)}" alt="" /></div>'


def build_five_beat_html(spec: ReelSpec) -> str:
    """Render the GenZ Capital five_beat reel as one HTML document.

    Element IDs and CSS classes are aligned with `reels/styles.css` (Reel #3
    palette: `.beat-stack`, `.line-setup`, `.line-punch`, `.line-sub`,
    `.insight-block`, `.proof-final`, `.cta-final`) and `reels/script.js`
    (animates `#b1a/b/c`, `#b2a/b/c`, `#b3a/b/c`, `#b4n/l/s`, `#b5a/b/c`).
    Topic-specific text comes from the spec; everything else stays brand-fixed.
    """
    audio_html = ""
    if spec.voiceover_src:
        audio_html += (
            f'\n      <audio id="vo" data-start="0" data-duration="30" '
            f'data-track-index="1" data-volume="{spec.voiceover_volume}" '
            f'src="{spec.voiceover_src}"></audio>'
        )
    if spec.music_src:
        audio_html += (
            f'\n      <audio id="music" data-start="0" data-duration="30" '
            f'data-track-index="2" data-volume="{spec.music_volume}" '
            f'src="{spec.music_src}"></audio>'
        )

    # cta_follow defaults to "FOLLOW @GENZ_CAPITALBUSINESS" — split into the
    # neon @handle (b5b) and the small "FOLLOW FOR MORE" foot (b5c).
    handle = "@GENZ_CAPITALBUSINESS"
    if spec.cta_follow and "@" in spec.cta_follow:
        handle = "@" + spec.cta_follow.split("@", 1)[1].strip().split()[0]

    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=1080, height=1920" />
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link
      href="https://fonts.googleapis.com/css2?family=Anton&family=Inter:wght@400;600;800&display=swap"
      rel="stylesheet"
    />
    <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
    <link rel="stylesheet" href="styles.css" />
  </head>
  <body>
    <div
      id="root"
      data-composition-id="main"
      data-start="0"
      data-duration="30"
      data-width="1080"
      data-height="1920"
    >
      <!-- BEAT 1 — HOOK (0–3s) -->
      <div id="b1" class="clip beat" data-start="0" data-duration="3" data-track-index="0">
        {_bg_media(spec.vid_hook, spec.img_hook)}
        <div class="vignette dark"></div>
        <div class="beat-stack">
          <div id="b1a" class="line-setup">{_esc(spec.hook_line_1)}</div>
          <div id="b1b" class="line-punch neon">{_esc(spec.hook_line_2)}</div>
          <div id="b1c" class="line-sub">{_esc(spec.hook_line_3)}</div>
        </div>
      </div>

      <!-- BEAT 2 — PROBLEM (3–10s) -->
      <div id="b2" class="clip beat" data-start="3" data-duration="7" data-track-index="0">
        {_bg_media(spec.vid_problem, spec.img_problem)}
        <div class="vignette dark"></div>
        <div class="beat-stack">
          <div id="b2a" class="line-setup">{_esc(spec.problem_a)}</div>
          <div id="b2b" class="line-setup">{_esc(spec.problem_b)}</div>
          <div id="b2c" class="line-sub">{_esc(spec.problem_c)}</div>
        </div>
      </div>

      <!-- BEAT 3 — INSIGHT (10–20s) -->
      <div id="b3" class="clip beat" data-start="10" data-duration="10" data-track-index="0">
        {_bg_media(spec.vid_insight, spec.img_insight)}
        <div class="vignette dark"></div>
        <div class="beat-stack">
          <div id="b3a" class="line-setup">{_esc(spec.insight_1)}</div>
          <div id="b3b" class="line-punch neon">{_esc(spec.insight_2)}</div>
          <div id="b3c" class="line-sub">{_esc(spec.insight_3)}</div>
        </div>
      </div>

      <!-- BEAT 4 — PROOF (20–27s) -->
      <div id="b4" class="clip beat" data-start="20" data-duration="7" data-track-index="0">
        <div class="black-bg"></div>
        <div class="proof-final">
          <div id="b4n" class="proof-big neon">{_esc(spec.proof_number)}</div>
          <div id="b4l" class="proof-lbl">{_esc(spec.proof_label)}</div>
          <div id="b4s" class="proof-sub">{_esc(spec.proof_sublabel)}</div>
        </div>
      </div>

      <!-- BEAT 5 — CTA (27–30s) -->
      <div id="b5" class="clip beat" data-start="27" data-duration="3" data-track-index="0">
        {_bg_media(spec.vid_cta, spec.img_cta)}
        <div class="vignette dark"></div>
        <div class="cta-final">
          <div id="b5a" class="cta-top">{_esc(spec.cta_save)}</div>
          <div id="b5b" class="cta-mid neon">{_esc(handle)}</div>
          <div id="b5c" class="cta-foot">FOLLOW FOR MORE</div>
        </div>
      </div>{audio_html}
    </div>

    <script src="script.js"></script>
    <script>
      window.__timelines = window.__timelines || {{}};
      if (!window.__timelines['main']) {{
        window.__timelines['main'] = (window.gsap || {{ timeline: () => ({{}}) }}).timeline({{ paused: true }});
      }}
    </script>
  </body>
</html>
"""


# ---------------------------------------------------------------------------
# Image picker — choose 4 distinct cinematic DALL-E images for the 5 beats.
# ---------------------------------------------------------------------------

def pick_images(needed: int = 4) -> list[str]:
    """Pick `needed` distinct image paths (relative to reels/) from generated/.

    Filters out variants that look like in-progress drafts (anything containing
    '_logo_' or '_v2'/'_v3' suffixes), keeping the cleanest cinematic outputs.
    """
    candidates = []
    if GENERATED_IMAGES_DIR.exists():
        candidates = sorted(
            f for f in GENERATED_IMAGES_DIR.iterdir()
            if f.suffix.lower() in {".jpg", ".jpeg", ".png"}
            and "_logo_" not in f.name
            and "_v" not in f.name
            and not f.name.startswith("ai_prompt_test_")
        )
    if not candidates:
        # No DALL-E backgrounds on disk. This is fine when the caller will
        # replace these with Pexels photos/videos (--pexels / --pexels-video);
        # the spec constructor still needs *something* in the img_* fields.
        # Fall back to the bundled placeholder so the spec can be built.
        log.warning(
            "No generated images in %s — using ../example_image.png as a "
            "placeholder. Run with --pexels or --pexels-video for real "
            "backgrounds, or add DALL-E images via post_generator.py.",
            GENERATED_IMAGES_DIR,
        )
        return ["../example_image.png"] * needed

    # Cycle through to guarantee `needed` unique-ish picks
    chosen = []
    for i in range(needed):
        chosen.append(candidates[i % len(candidates)])
    rels = [f"../assets/images/generated/{p.name}" for p in chosen]
    log.info("Picked images: %s", [Path(p).name for p in rels])
    return rels


# ---------------------------------------------------------------------------
# Slug helper
# ---------------------------------------------------------------------------

def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:40] or "reel"


_PEXELS_STOP = {
    "the", "and", "for", "you", "are", "was", "but", "not", "all",
    "this", "that", "they", "their", "with", "from", "have", "has",
    "will", "wont", "your", "ours", "now", "why", "how", "who",
    "what", "ever", "never", "here", "its", "his", "her", "our",
}


def _query_from(*parts: str, fallback: str = "") -> str:
    """Reduce text to 3 concrete search tokens (filler + short words stripped)."""
    raw = " ".join(parts).lower()
    tokens = [t for t in re.findall(r"[a-z]+", raw) if len(t) > 2]
    keep = [t for t in tokens if t not in _PEXELS_STOP]
    return " ".join(keep[:3]) or fallback


def _derive_pexels_query(spec: "ReelSpec") -> str:
    """Build a single Pexels search query from a five_beat spec (photo mode)."""
    return _query_from(
        spec.hook_line_1, spec.hook_line_3, spec.insight_3,
        fallback=spec.slug.replace("-", " ") or "cinematic technology",
    )


def _derive_pexels_queries(spec: "ReelSpec") -> list[str]:
    """One Pexels query per beat (hook, problem, insight, cta).

    Each beat searches on its own text, so the clip matches what that beat is
    actually about — a far better topical match than one query for the whole
    reel, especially for niche subjects (AI, tech, business).
    """
    seed = spec.slug.replace("-", " ") or "cinematic technology"
    return [
        _query_from(spec.hook_line_1, spec.hook_line_2, spec.hook_line_3, fallback=seed),
        _query_from(spec.problem_a, spec.problem_b, spec.problem_c, fallback=seed),
        _query_from(spec.insight_1, spec.insight_2, spec.insight_3, fallback=seed),
        _query_from(spec.cta_save, fallback=seed),
    ]


# ---------------------------------------------------------------------------
# Render — invoke the npm script that wraps `hyperframes render` with PATH set.
# ---------------------------------------------------------------------------

def render_reel(spec: ReelSpec) -> Path:
    """Write index.html, run hyperframes render, return path to the MP4."""
    REELS_DIR.mkdir(parents=True, exist_ok=True)
    ASSETS_REELS_DIR.mkdir(parents=True, exist_ok=True)

    html = build_reel_html(spec)
    (REELS_DIR / "index.html").write_text(html, encoding="utf-8")
    log.info("Wrote reels/index.html (%d bytes, template=%s)", len(html), spec.template)

    # The GSAP script bound to the HTML differs per template — Montage Hook needs
    # the archived Reel #1 timeline (montage cycle + question/reveal/cliffhanger),
    # while the 5-beat template has its own animations in the live script.js.
    if spec.template == "montage_hook":
        archived_js = REELS_ARCHIVE_DIR / "script_reel1.js"
        live_js = REELS_DIR / "script.js"
        live_js.write_text(archived_js.read_text(encoding="utf-8"), encoding="utf-8")
        log.info("Copied archive/script_reel1.js -> reels/script.js")

    # Lint first — fail fast if the composition is broken
    npm = "npm.cmd" if os.name == "nt" else "npm"
    lint = subprocess.run(
        [npm, "run", "reels:lint"], cwd=REPO_ROOT,
        capture_output=True, text=True,
    )
    if lint.returncode != 0:
        log.error("Lint failed:\n%s", lint.stdout + lint.stderr)
        raise RuntimeError("hyperframes lint reported errors")
    log.info("Lint passed.")

    out_path = ASSETS_REELS_DIR / f"{spec.slug}.mp4"
    cmd = [
        "node", str(REPO_ROOT / "scripts" / "with-ffmpeg.js"),
        "hyperframes", "render", "reels",
        "--quality", spec.quality,
        "--fps", str(spec.fps),
        "--workers", "2",   # Chrome workers crash above ~4 on this Windows machine
        "-o", str(out_path),
    ]
    log.info("Rendering: %s", " ".join(cmd))
    proc = subprocess.run(cmd, cwd=REPO_ROOT)
    if proc.returncode != 0:
        raise RuntimeError(f"hyperframes render failed (exit {proc.returncode})")

    if not out_path.exists():
        raise RuntimeError(f"Render reported success but {out_path} is missing")
    log.info("Rendered: %s (%.1f MB)", out_path, out_path.stat().st_size / 1e6)
    return out_path


# ---------------------------------------------------------------------------
# Sample / spec / Sheets entry points
# ---------------------------------------------------------------------------

def sample_spec() -> ReelSpec:
    imgs = pick_images(4)
    return ReelSpec(
        slug="sample-home-cost",
        hook_line_1="99% OF PEOPLE",
        hook_line_2="WILL NEVER",
        hook_line_3="OWN A HOME",
        hook_subline="...AND HERE'S WHY.",
        problem_a="THE REASON?",
        problem_b="THEY DON'T KNOW THIS.",
        insight_1="HOMES COST",
        insight_2="14X INCOME",
        insight_3="UP FROM 3X.",
        proof_number="$1.4M",
        proof_label="AVG U.S. STARTER HOME · 2040",
        img_hook=imgs[0],
        img_problem=imgs[1],
        img_insight=imgs[2],
        img_cta=imgs[3],
    )


def spec_from_json(path: Path) -> ReelSpec:
    data = json.loads(path.read_text(encoding="utf-8"))
    if "slug" not in data:
        data["slug"] = slugify(data.get("hook_line_1", "reel") + "-" +
                               datetime.utcnow().strftime("%Y%m%d"))
    return ReelSpec(**data)


def spec_from_sheet_row(row: dict) -> ReelSpec:
    """Build a ReelSpec from a Google Sheets row populated by research_topic.py.

    Reads the structured headline / proof / template columns rather than parsing
    `reel_script` line-by-line. Falls back to legacy script-line parsing only
    when the structured columns are empty (so older draft rows still work).
    """
    topic = row.get("Topic", "").strip() or "Untitled"
    template = (row.get("Reel Template") or "").strip().lower() or "five_beat"

    headline_1 = (row.get("Headline Line 1 (White)") or "").strip()
    headline_2 = (row.get("Headline Line 2 (Neon Green)") or "").strip()
    headline_3 = (row.get("Headline Line 3 (White)") or "").strip()
    subhead = (row.get("Subheadline (Gray)") or "").strip()
    proof_number = (row.get("Key Stat") or "").strip()
    proof_label = (row.get("Key Points") or "").strip()
    reel_script = (row.get("Reel Script") or row.get("reel_script") or "").strip()

    lines = [ln.strip() for ln in reel_script.splitlines() if ln.strip()]
    while len(lines) < 5:
        lines.append("")

    slug_seed = f"{topic}-{datetime.utcnow().strftime('%Y%m%d')}"
    slug = slugify(slug_seed)

    # Voiceover MP3 path is conventionally produced by generate_reel_voiceover.py
    # at reels/assets/audio/<slug>_voiceover.mp3 — relative to reels/index.html
    # that's "assets/audio/<slug>_voiceover.mp3".
    voiceover_src = f"assets/audio/{slug}_voiceover.mp3"

    if template == "montage_hook":
        return ReelSpec(
            slug=slug,
            template="montage_hook",
            reveal_line_1=(row.get("_reveal_line_1") or "ONE NICHE.").upper(),
            reveal_line_2=(row.get("_reveal_line_2") or "ONE FORMAT.").upper(),
            reveal_line_3=(row.get("_reveal_line_3") or "POSTED DAILY.").upper(),
            cliffhanger_count=(row.get("_cliffhanger_count") or "4 MORE").upper(),
            cliffhanger_label=(row.get("_cliffhanger_label") or "TRICKS").upper(),
            cta_comment_word=(row.get("_cta_comment_word") or "NEXT").upper(),
            proof_number=proof_number or "30M+",
            proof_label=proof_label[:48].upper() or topic.upper()[:48],
            voiceover_src=voiceover_src,
        )

    imgs = pick_images(4)
    return ReelSpec(
        slug=slug,
        template="five_beat",
        hook_line_1=(headline_1 or lines[0])[:24].upper() or topic.upper()[:24],
        hook_line_2=(headline_2 or "WAKE UP")[:14].upper(),
        hook_line_3=(headline_3 or lines[2] or "NOW.")[:24].upper(),
        hook_subline=(subhead or "...AND HERE'S WHY.").upper(),
        problem_a="THE REASON?",
        problem_b=(lines[1][:32] or proof_label[:32]).upper() or "THEY DON'T KNOW THIS.",
        insight_1=(lines[2][:18] or "THE TRUTH IS").upper(),
        insight_2=(headline_2 or lines[3][:14] or "RIGHT NOW")[:14].upper(),
        insight_3=(lines[4][:18] or "EVERYTHING SHIFTS").upper(),
        proof_number=proof_number or "$1M+",
        proof_label=proof_label[:48].upper() or topic.upper()[:48],
        img_hook=imgs[0],
        img_problem=imgs[1],
        img_insight=imgs[2],
        img_cta=imgs[3],
        voiceover_src=voiceover_src,
    )


def load_sheet_row(row_index: int | None = None):
    """Lazy-import gspread / config and pull a row from the Sheet.

    If `row_index` is given, fetch that exact row. Otherwise pull the next
    'Ready' row.

    Returns (row_dict, sheets_reader) so the caller can mark_published later.
    """
    sys.path.insert(0, str(REPO_ROOT / "publisher"))
    # Reuse the SheetsReader from post_generator.py
    from post_generator import SheetsReader  # type: ignore
    # Reels live on their own tab — Sheet1 is for posts, Sheet2 for carousels.
    reels_tab = os.getenv("GOOGLE_SHEET_REELS_NAME", "Reels")
    config_path = REPO_ROOT / "publisher" / "config.json"
    if not config_path.exists():
        config = {
            "google_sheets": {
                "credentials_file": "google_service_account.json",
                "spreadsheet_id": os.getenv("GOOGLE_SHEET_ID", ""),
                "sheet_name": reels_tab,
            }
        }
    else:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["google_sheets"]["spreadsheet_id"] = (
            config["google_sheets"].get("spreadsheet_id")
            or os.getenv("GOOGLE_SHEET_ID", "")
        )
        config["google_sheets"]["sheet_name"] = reels_tab

    reader = SheetsReader(config)
    if row_index is not None:
        all_values = reader.ws.get_all_values()
        if row_index < 2 or row_index > len(all_values):
            return None, reader
        headers = all_values[0]
        raw = all_values[row_index - 1]
        row = {headers[j]: raw[j] if j < len(raw) else "" for j in range(len(headers))}
        row["_row_index"] = row_index
        return row, reader
    row = reader.get_next_ready_row()
    return row, reader


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Render a GenZ Capital reel via HyperFrames.")
    parser.add_argument("--sample", action="store_true",
                        help="Render with hard-coded sample data (no Sheets, no APIs).")
    parser.add_argument("--spec", type=Path, default=None,
                        help="Path to a JSON file matching the ReelSpec schema.")
    parser.add_argument("--quality", default="standard",
                        choices=["draft", "standard", "high"],
                        help="Render quality (draft is fastest).")
    parser.add_argument("--fps", type=int, default=30, choices=[24, 30, 60])
    parser.add_argument("--from-sheet", type=int, default=None,
                        help="Render this exact Google Sheets row index "
                             "(1-based, including header).")
    parser.add_argument("--dump-html-only", action="store_true",
                        help="Write reels/index.html but skip the render.")
    parser.add_argument("--pexels", action="store_true",
                        help="Replace the 4 background images with copyright-safe "
                             "Pexels stock photos (requires PEXELS_API_KEY in .env).")
    parser.add_argument("--pexels-video", action="store_true",
                        help="Use Pexels portrait *videos* as beat backgrounds "
                             "(downloads + pre-cuts one clip per beat). Beat 4 "
                             "(proof) stays solid black.")
    parser.add_argument("--pexels-query", default=None,
                        help="Override the Pexels search query. Default: derived "
                             "from the topic / hook lines.")
    args = parser.parse_args()

    if args.sample:
        spec = sample_spec()
    elif args.spec:
        spec = spec_from_json(args.spec)
    else:
        if args.from_sheet:
            log.info("Reading row %d from Google Sheets...", args.from_sheet)
            row, _reader = load_sheet_row(row_index=args.from_sheet)
        else:
            log.info("Reading next 'Ready' row from Google Sheets...")
            row, _reader = load_sheet_row()
        if not row:
            log.error("No matching row in the sheet. Use --sample for a smoke test.")
            sys.exit(1)
        log.info("Topic: %s", row.get("Topic", ""))
        spec = spec_from_sheet_row(row)

    spec.quality = args.quality
    spec.fps = args.fps

    # Auto-picked media (from publisher/media_finder.py) — used when no
    # explicit --pexels / --pexels-video override is given. Only applies
    # when we actually read a row from the sheet.
    auto_video_url = ""
    auto_image_url = ""
    if not args.sample and not args.spec:
        auto_video_url = (row.get("Media Video URL") or "").strip()
        auto_image_url = (row.get("Media Image URL") or "").strip()

    if auto_video_url and not args.pexels_video and spec.template == "five_beat":
        log.info("Auto media: using Media Video URL from sheet (%s)", auto_video_url)
        from publisher.media_consumer import fetch_video  # type: ignore
        durations = [3, 7, 10, 3]
        clip_rels = fetch_video(auto_video_url, slug=spec.slug, durations=durations)
        spec.vid_hook, spec.vid_problem, spec.vid_insight, spec.vid_cta = clip_rels

    if auto_image_url and not args.pexels and spec.template == "five_beat":
        log.info("Auto media: using Media Image URL from sheet (%s)", auto_image_url)
        from publisher.media_consumer import fetch_image  # type: ignore
        img_rels = fetch_image(auto_image_url, slug=spec.slug, count=4)
        spec.img_hook, spec.img_problem, spec.img_insight, spec.img_cta = img_rels

    if args.pexels and spec.template == "five_beat":
        query = args.pexels_query or _derive_pexels_query(spec)
        log.info("Pexels mode ON — query=%r", query)
        sys.path.insert(0, str(REPO_ROOT))
        from scripts.pexels_fetcher import search_and_download  # type: ignore
        pexels_rels = search_and_download(query, count=4, slug=spec.slug)
        spec.img_hook, spec.img_problem, spec.img_insight, spec.img_cta = pexels_rels
    elif args.pexels and spec.template == "montage_hook":
        log.warning("--pexels has no effect on montage_hook (uses fixed brand screenshots).")

    if args.pexels_video and spec.template == "five_beat":
        sys.path.insert(0, str(REPO_ROOT))
        from scripts.pexels_fetcher import search_and_download_videos_multi  # type: ignore
        # Beat durations: hook=3, problem=7, insight=10, cta=3 (skip beat 4 — proof on black bg)
        durations = [3, 7, 10, 3]
        if args.pexels_query:
            queries = [args.pexels_query] * 4          # single override -> all beats
        elif len(spec.pexels_queries) == 4:
            queries = spec.pexels_queries               # explicit per-beat queries
        else:
            queries = _derive_pexels_queries(spec)      # derived per-beat queries
        log.info("Pexels VIDEO mode ON — per-beat queries=%s", queries)
        clip_rels = search_and_download_videos_multi(
            queries, durations, slug=spec.slug,
        )
        spec.vid_hook, spec.vid_problem, spec.vid_insight, spec.vid_cta = clip_rels
    elif args.pexels_video and spec.template == "montage_hook":
        log.warning("--pexels-video has no effect on montage_hook.")

    log.info("Spec:\n%s", json.dumps(asdict(spec), indent=2))

    if args.dump_html_only:
        html = build_reel_html(spec)
        (REELS_DIR / "index.html").write_text(html, encoding="utf-8")
        log.info("Wrote reels/index.html — skipping render (per --dump-html-only).")
        return

    out = render_reel(spec)
    print(str(out))


if __name__ == "__main__":
    main()
