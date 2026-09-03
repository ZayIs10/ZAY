"""publisher/speech_captions.py — word-pop caption frames for the
motivation-speech reel format (@peakzmotivation style).

Takes per-word timings (publisher/media_sources/word_timing.py) and produces:
  * one transparent RGBA PNG per caption STATE (words accumulate within a
    2-3 word "page": "NOTHING" → "NOTHING IS" → "NOTHING IS IMPOSSIBLE"),
  * a `captions.ffconcat` list feeding those PNGs to ffmpeg as ONE concat-
    demuxer input (a single overlay filter — scales to any word count, unlike
    per-word overlay/drawtext chains),
  * a static full-canvas gradient scrim so the text always reads over footage.

Styling matches the locked brand language: Anton uppercase, white
(245,246,248) with a heavy black stroke, important words in neon #39FF14.
Important-word selection is a free deterministic heuristic (numbers, a power-
word list, ALL-CAPS from the transcript) — no GPT, mirroring carousel_format's
_neon_set philosophy of 1-2 neon words per visible page.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger("speech_captions")

_REPO_ROOT = Path(__file__).resolve().parents[1]
_ANTON = _REPO_ROOT / "fonts" / "Anton-Regular.ttf"

CANVAS_W, CANVAS_H = 1080, 1920
BAND_W, BAND_H = 1080, 560
BAND_Y = 1020            # band's top edge; text centers around y≈1300 (~68%)
_MARGIN = 70             # matches carousel_format's safe margin
_MAX_TEXT_W = BAND_W - 2 * _MARGIN

WHITE = (245, 246, 248, 255)
NEON = (57, 255, 20, 255)          # #39FF14 — Gen Z Capital signature
_STROKE = (0, 0, 0, 255)
_SHADOW = (0, 0, 0, 170)

_FONT_START, _FONT_MIN = 104, 64
_CURRENT_WORD_SCALE = 1.08          # the just-spoken word pops slightly larger

_FPS = 30.0
_FRAME = 1.0 / _FPS

# Pagination knobs
MAX_WORDS_PER_PAGE = 3
PAUSE_BREAK_SECONDS = 0.6           # a silence this long starts a fresh page
_PAGE_HOLD_SECONDS = 0.35           # completed page lingers before a blank gap
_SENTENCE_END_RE = re.compile(r"[.!?]$")

POWER_WORDS = frozenset({
    "never", "nothing", "nobody", "impossible", "possible", "success",
    "succeed", "fail", "failure", "failed", "fear", "afraid", "dream",
    "dreams", "work", "hard", "quit", "pain", "win", "winner", "lose",
    "loser", "rich", "poor", "money", "discipline", "sacrifice", "greatness",
    "great", "believe", "belief", "destiny", "hustle", "grind", "champion",
    "fight", "power", "strong", "strength", "weak", "change", "future",
    "risk", "courage", "brave", "excuse", "excuses", "opportunity", "chance",
    "purpose", "focus", "obsessed", "relentless", "unstoppable", "everything",
    "yourself", "responsibility", "die", "alive", "life", "time", "now",
})


def _norm_token(word: str) -> str:
    return re.sub(r"[^a-z0-9$%]", "", word.lower())


def pick_power_words(words: list[str]) -> set[int]:
    """Indices of words to paint neon: any token with a digit/$/%, any
    POWER_WORDS member, any ALL-CAPS transcript token (3+ letters)."""
    hits: set[int] = set()
    for i, w in enumerate(words):
        tok = _norm_token(w)
        if not tok:
            continue
        if any(c.isdigit() for c in tok) or "$" in tok or "%" in tok:
            hits.add(i)
        elif tok in POWER_WORDS:
            hits.add(i)
        elif len(w) >= 3 and w.isupper() and w.isalpha():
            hits.add(i)
    return hits


def paginate(word_timings: list[dict], *, body_max: float,
             max_words: int = MAX_WORDS_PER_PAGE,
             pause_break: float = PAUSE_BREAK_SECONDS) -> list[list[dict]]:
    """Group timed words into caption pages of <= max_words, breaking early on
    a silence > pause_break or sentence-ending punctuation. Words at/after
    `body_max` (video is trimmed there) are dropped. Each word dict gains a
    'neon' flag from pick_power_words (computed over the WHOLE speech so a
    page never re-derives context)."""
    kept = [dict(w) for w in word_timings if w["start"] < body_max - 0.2]
    if not kept:
        return []
    neon_idx = pick_power_words([w["word"] for w in kept])
    for i, w in enumerate(kept):
        w["neon"] = i in neon_idx
        w["end"] = min(w["end"], body_max)

    pages: list[list[dict]] = []
    page: list[dict] = []
    for w in kept:
        if page:
            gap = w["start"] - page[-1]["end"]
            if (len(page) >= max_words or gap > pause_break
                    or _SENTENCE_END_RE.search(page[-1]["word"])):
                pages.append(page)
                page = []
        page.append(w)
    if page:
        pages.append(page)

    # Guarantee every page has at least one neon word (longest word >= 6
    # chars) so the green energy never disappears for long stretches.
    for p in pages:
        if not any(w["neon"] for w in p):
            best = max(p, key=lambda w: len(_norm_token(w["word"])))
            if len(_norm_token(best["word"])) >= 6:
                best["neon"] = True
    return pages


def _font(size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(str(_ANTON), size)
    except OSError:
        return ImageFont.load_default()


def _layout(draw: ImageDraw.ImageDraw, words: list[dict],
            current_idx: int) -> tuple[list[list[tuple[dict, ImageFont.FreeTypeFont, int]]], int]:
    """Pick the largest Anton size (<= 2 lines) where the page fits the band.
    Returns (lines, line_height); each line is [(word, font, width), ...].
    The current (just-spoken) word renders _CURRENT_WORD_SCALE larger."""
    size = _FONT_START
    while size >= _FONT_MIN:
        base = _font(size)
        cur = _font(int(size * _CURRENT_WORD_SCALE))
        space_w = draw.textlength(" ", font=base)
        measured = []
        for i, w in enumerate(words):
            f = cur if i == current_idx else base
            measured.append((w, f, int(draw.textlength(
                w["word"].upper(), font=f))))
        # Greedy wrap into at most 2 lines.
        lines: list[list[tuple[dict, ImageFont.FreeTypeFont, int]]] = [[]]
        x = 0
        ok = True
        for item in measured:
            need = item[2] + (space_w if lines[-1] else 0)
            if x + need > _MAX_TEXT_W and lines[-1]:
                if len(lines) == 2:
                    ok = False
                    break
                lines.append([])
                x = 0
                need = item[2]
            lines[-1].append(item)
            x += need
        if ok:
            return lines, int(size * _CURRENT_WORD_SCALE * 1.18)
        size -= 4
    # Nothing fit even at the minimum size (pathological input) — lay out at
    # the minimum with as many lines as it takes rather than truncating.
    base = _font(_FONT_MIN)
    cur = _font(int(_FONT_MIN * _CURRENT_WORD_SCALE))
    space_w = draw.textlength(" ", font=base)
    lines = [[]]
    x = 0
    for i, w in enumerate(words):
        f = cur if i == current_idx else base
        ww = int(draw.textlength(w["word"].upper(), font=f))
        need = ww + (space_w if lines[-1] else 0)
        if x + need > _MAX_TEXT_W and lines[-1]:
            lines.append([])
            x = 0
            need = ww
        lines[-1].append((w, f, ww))
        x += need
    return lines, int(_FONT_MIN * _CURRENT_WORD_SCALE * 1.18)


def _render_state(words: list[dict], current_idx: int, out_png: Path) -> None:
    """One caption state: `words` [0..current_idx] visible, centered in the
    band, neon words green, current word slightly larger, black stroke +
    soft drop shadow for legibility over any footage."""
    img = Image.new("RGBA", (BAND_W, BAND_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    visible = words[: current_idx + 1]
    lines, line_h = _layout(draw, visible, current_idx)

    total_h = line_h * len(lines)
    y = max(0, (BAND_H - total_h) // 2)
    for line in lines:
        space_w = draw.textlength(" ", font=line[0][1]) if line else 0
        line_w = sum(w for _, _, w in line) + space_w * (len(line) - 1)
        x = (BAND_W - int(line_w)) // 2
        for w, f, ww in line:
            text = w["word"].upper()
            fill = NEON if w.get("neon") else WHITE
            stroke = max(4, f.size // 14)
            draw.text((x, y + 6), text, font=f, fill=_SHADOW,
                      stroke_width=stroke, stroke_fill=_SHADOW)
            draw.text((x, y), text, font=f, fill=fill,
                      stroke_width=stroke, stroke_fill=_STROKE)
            x += ww + int(space_w)
        y += line_h
    img.save(out_png)


def render_scrim(work_dir: Path) -> Path:
    """Static full-canvas gradient scrim (transparent → soft black behind the
    caption band → transparent) overlaid for the whole body so text stays
    readable and nothing flickers when caption states change."""
    work_dir.mkdir(parents=True, exist_ok=True)
    out = work_dir / "scrim.png"
    img = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 0))
    px = img.load()
    top_fade, plateau_a, plateau_b, bottom_fade = 950, 1150, 1500, 1700
    max_alpha = 145
    for y in range(CANVAS_H):
        if y < top_fade or y > bottom_fade:
            a = 0
        elif y < plateau_a:
            a = int(max_alpha * (y - top_fade) / (plateau_a - top_fade))
        elif y <= plateau_b:
            a = max_alpha
        else:
            a = int(max_alpha * (bottom_fade - y) / (bottom_fade - plateau_b))
        if a:
            for x in range(CANVAS_W):
                px[x, y] = (0, 0, 0, a)
    img.save(out)
    return out


def _q(t: float) -> float:
    """Quantize a timestamp to the 30fps frame grid (absolute, so rounding
    never accumulates drift across a 60s speech)."""
    return round(t * _FPS) / _FPS


def render_caption_states(pages: list[list[dict]], work_dir: Path,
                          *, body_max: float) -> Path:
    """Render every caption state PNG and write `captions.ffconcat` describing
    when each is on screen. Blank (fully transparent) frames cover the gaps —
    before the first word, in pauses between pages, and after the last page —
    so the single overlay filter needs no enable= expressions at all."""
    states_dir = work_dir / "cap_states"
    states_dir.mkdir(parents=True, exist_ok=True)

    blank = states_dir / "blank.png"
    Image.new("RGBA", (BAND_W, BAND_H), (0, 0, 0, 0)).save(blank)

    # (png_path, start, end) in absolute body time, frame-quantized.
    timeline: list[tuple[Path, float, float]] = []
    n = 0
    for pi, page in enumerate(pages):
        next_start = (pages[pi + 1][0]["start"] if pi + 1 < len(pages)
                      else min(page[-1]["end"] + _PAGE_HOLD_SECONDS, body_max))
        for wi, w in enumerate(page):
            png = states_dir / f"state_{n:03d}.png"
            _render_state(page, wi, png)
            n += 1
            start = w["start"]
            if wi + 1 < len(page):
                end = page[wi + 1]["start"]
            else:  # last word: hold the finished page, then blank the gap
                end = min(max(w["end"], w["start"] + 0.2)
                          + _PAGE_HOLD_SECONDS, next_start
                          if pi + 1 < len(pages) else body_max)
            start, end = _q(start), _q(end)
            if end - start < _FRAME:
                end = start + _FRAME
            timeline.append((png, start, end))

    # Stitch into a gapless ffconcat: blanks fill every hole.
    lines = ["ffconcat version 1.0"]
    cursor = 0.0
    for png, start, end in timeline:
        if start > cursor + _FRAME / 2:
            lines.append(f"file '{blank.name}'")
            lines.append(f"duration {start - cursor:.5f}")
            cursor = start
        if end <= cursor + _FRAME / 2:   # overlapping cue — skip zero states
            continue
        lines.append(f"file '{png.name}'")
        lines.append(f"duration {end - max(cursor, start):.5f}")
        cursor = end
    # Trailing blank so nothing lingers after the final page, plus the
    # concat-demuxer convention of repeating the last file.
    tail = max(body_max - cursor, _FRAME)
    lines.append(f"file '{blank.name}'")
    lines.append(f"duration {tail:.5f}")
    lines.append(f"file '{blank.name}'")

    ffconcat = states_dir / "captions.ffconcat"
    ffconcat.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.info("Caption states: %d PNGs, timeline 0→%.1fs → %s",
             n, cursor, ffconcat)
    return ffconcat
