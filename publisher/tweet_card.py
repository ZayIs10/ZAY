"""Render a static "X/Twitter post" caption card as RGBA PNG.

Used by publisher/tweet_card_reel.py — the new @execute-style reel
format that pins this card on top of a 1080x1920 canvas with the
source-company video playing in a rect below it.

Pure I/O: read the avatar, write the PNG. No Sheet / network calls.

Public API:
    render(text, handle, display_name, avatar_path, out_path,
           *, signoff=DEFAULT_SIGNOFF) -> Path
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont

REPO_ROOT = Path(__file__).resolve().parents[1]
FONTS_DIR = REPO_ROOT / "fonts"

CARD_W = 1000
CARD_H = 720
CARD_RADIUS = 28
CARD_PAD_X = 40
CARD_PAD_TOP = 32
CARD_PAD_BOTTOM = 28
CARD_BG = (21, 24, 28, 245)        # near-black, slight transparency lets bg tile show through
TEXT_WHITE = (231, 233, 234, 255)  # X dark-mode body
TEXT_DIM = (113, 118, 123, 255)    # X dark-mode meta
CHECK_BLUE = (29, 155, 240, 255)

AVATAR_SIZE = 88
HANDLE_GAP = 16
HEADER_HEIGHT = AVATAR_SIZE + 12
BODY_FONT_SIZE = 30
BODY_LINE_SPACING = 10
NAME_FONT_SIZE = 28
HANDLE_FONT_SIZE = 24
SIGNOFF_FONT_SIZE = 22

DEFAULT_SIGNOFF = "We only post the best AI tools & how-tos"


# ---------------------------------------------------------------------------

_WIN_FALLBACK = Path("C:/Windows/Fonts")
_FALLBACKS: dict[str, list[str]] = {
    "Inter-Regular.ttf": ["segoeui.ttf", "arial.ttf", "tahoma.ttf"],
    "Inter-Bold.ttf": ["segoeuib.ttf", "arialbd.ttf"],
    "Anton-Regular.ttf": ["impact.ttf", "arialbd.ttf"],
}


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    """Load a font with graceful fallback. The committed `fonts/Inter-Regular.ttf`
    has been corrupted at least once (HTML download mislabeled as TTF), so
    if the primary file fails we try the platform's bundled equivalent."""
    primary = FONTS_DIR / name
    try:
        return ImageFont.truetype(str(primary), size)
    except (OSError, IOError):
        pass
    for fb_name in _FALLBACKS.get(name, []):
        fb_path = _WIN_FALLBACK / fb_name
        if fb_path.exists():
            try:
                return ImageFont.truetype(str(fb_path), size)
            except (OSError, IOError):
                continue
    return ImageFont.load_default(size=size)


def _circular_avatar(src_path: Path, size: int) -> Image.Image:
    """Load src_path, crop to centered square, mask to circle, return RGBA.

    Mirrors the alpha-aware crop trick used in
    publisher/post_generator.py:_draw_logo (lines 414-437) — extract the
    actual logo bounding box from the alpha channel so any white-bg
    padding doesn't make the avatar tiny inside the circle.
    """
    img = Image.open(src_path).convert("RGBA")
    bbox = img.split()[3].getbbox()
    if bbox:
        img = img.crop(bbox)

    side = max(img.size)
    canvas = Image.new("RGBA", (side, side), (255, 255, 255, 0))
    canvas.paste(img, ((side - img.width) // 2, (side - img.height) // 2), img)
    canvas = canvas.resize((size, size), Image.LANCZOS)

    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(canvas, (0, 0), mask)
    return out


def _wrap_to_width(
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
    draw: ImageDraw.ImageDraw,
) -> list[str]:
    """Greedy word-wrap honoring max_width in pixels. Single long words
    are kept on their own line (no hyphenation)."""
    lines: list[str] = []
    for paragraph in text.split("\n"):
        if not paragraph.strip():
            lines.append("")
            continue
        words = paragraph.split()
        current = ""
        for w in words:
            candidate = w if not current else f"{current} {w}"
            if draw.textlength(candidate, font=font) <= max_width:
                current = candidate
            else:
                if current:
                    lines.append(current)
                current = w
        if current:
            lines.append(current)
    return lines


def _truncate_to_fit(
    lines: list[str],
    max_lines: int,
) -> list[str]:
    """If body overflows max_lines, drop trailing lines and add an ellipsis
    to the last surviving line at a sentence boundary if possible."""
    if len(lines) <= max_lines:
        return lines
    kept = lines[:max_lines]
    last = kept[-1]
    for sep in (". ", "! ", "? "):
        idx = last.rfind(sep)
        if idx > 20:
            kept[-1] = last[: idx + 1] + " …"
            return kept
    kept[-1] = last.rstrip(",;:") + " …"
    return kept


def _draw_check(draw: ImageDraw.ImageDraw, cx: int, cy: int, r: int) -> None:
    """Twitter blue verified check — circle with a white tick."""
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=CHECK_BLUE)
    tick = [
        (cx - r * 0.45, cy + r * 0.05),
        (cx - r * 0.10, cy + r * 0.45),
        (cx + r * 0.55, cy - r * 0.30),
    ]
    draw.line(tick, fill=(255, 255, 255, 255), width=max(2, r // 4))


# ---------------------------------------------------------------------------

def render(
    text: str,
    *,
    handle: str = "@genzcapital",
    display_name: str = "Gen Z Capital",
    avatar_path: Path,
    out_path: Path,
    signoff: str = DEFAULT_SIGNOFF,
    width: int = CARD_W,
    height: int = CARD_H,
) -> Path:
    """Render a tweet-style caption card to `out_path` as RGBA PNG.

    `text` is the body. Long bodies are truncated at a sentence boundary
    so the card never overflows. `width`/`height` are fixed by default
    (1000x720) so the downstream compositor knows exactly where to put
    the video rectangle below.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)

    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Rounded card background.
    draw.rounded_rectangle(
        (0, 0, width - 1, height - 1),
        radius=CARD_RADIUS,
        fill=CARD_BG,
    )

    name_font = _font("Anton-Regular.ttf", NAME_FONT_SIZE + 4)
    handle_font = _font("Inter-Regular.ttf", HANDLE_FONT_SIZE)
    body_font = _font("Inter-Regular.ttf", BODY_FONT_SIZE)
    signoff_font = _font("Inter-Regular.ttf", SIGNOFF_FONT_SIZE)

    # Avatar (circle, top-left of the card content area).
    avatar = _circular_avatar(avatar_path, AVATAR_SIZE)
    avatar_x = CARD_PAD_X
    avatar_y = CARD_PAD_TOP
    img.paste(avatar, (avatar_x, avatar_y), avatar)

    # Header: display name + check, then "@handle" below.
    header_x = avatar_x + AVATAR_SIZE + HANDLE_GAP
    name_y = avatar_y + 6
    draw.text((header_x, name_y), display_name, font=name_font, fill=TEXT_WHITE)

    name_w = draw.textlength(display_name, font=name_font)
    check_r = NAME_FONT_SIZE // 2
    check_cx = int(header_x + name_w + 14 + check_r)
    check_cy = int(name_y + (NAME_FONT_SIZE + 4) / 2)
    _draw_check(draw, check_cx, check_cy, check_r)

    handle_y = name_y + NAME_FONT_SIZE + 10
    draw.text((header_x, handle_y), handle, font=handle_font, fill=TEXT_DIM)

    # Body text — wrap, truncate, draw.
    body_top = avatar_y + HEADER_HEIGHT + 20
    body_max_width = width - 2 * CARD_PAD_X
    line_h = BODY_FONT_SIZE + BODY_LINE_SPACING
    signoff_h = SIGNOFF_FONT_SIZE + 8
    available_h = height - body_top - CARD_PAD_BOTTOM - signoff_h - 16
    max_lines = max(3, available_h // line_h)

    raw_lines = _wrap_to_width(text.strip(), body_font, body_max_width, draw)
    lines = _truncate_to_fit(raw_lines, max_lines)

    y = body_top
    for line in lines:
        draw.text((CARD_PAD_X, y), line, font=body_font, fill=TEXT_WHITE)
        y += line_h

    # Signoff pinned to bottom.
    if signoff:
        signoff_y = height - CARD_PAD_BOTTOM - SIGNOFF_FONT_SIZE
        draw.text(
            (CARD_PAD_X, signoff_y),
            signoff,
            font=signoff_font,
            fill=TEXT_DIM,
        )

    img.save(out_path, "PNG")
    return out_path


# ---------------------------------------------------------------------------
# CLI: useful for eyeballing the card without the full pipeline.

def _cli(argv: Iterable[str]) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Render a tweet-card PNG.")
    p.add_argument("--text", required=True)
    p.add_argument("--handle", default="@genzcapital")
    p.add_argument("--name", default="Gen Z Capital")
    p.add_argument("--avatar", default=str(REPO_ROOT / "logo.png"))
    p.add_argument("--out", default=str(REPO_ROOT / ".tmp" / "tweet_card.png"))
    args = p.parse_args(list(argv))

    out = render(
        args.text,
        handle=args.handle,
        display_name=args.name,
        avatar_path=Path(args.avatar),
        out_path=Path(args.out),
    )
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(_cli(sys.argv[1:]))
