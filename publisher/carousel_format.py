"""
carousel_format.py — Gen Z Capital carousel POST FORMAT (no API calls, no cost).

This is the reusable LAYOUT ENGINE for @evolving.ai-style Instagram carousels,
rebranded to Gen Z Capital. Feed it a topic + a list of slide "beats" and it
renders a complete swipeable carousel into  assets/carousels/<slug>/ .

WHY THIS EXISTS
---------------
`carousel_generator.py` is the *pipeline* (GPT writes slides -> DALL-E images ->
Instagram). It uses the OLD watcher.guru-ish layout and costs money every run.
This file is the *format/template* the user asked for: the visual language copied
from @evolving.ai, with Gen Z Capital's brand baked in. You can:
  - run it as-is to see a real sample carousel (uses local images / painted bg)
  - import build_carousel() from the pipeline to render with real DALL-E images

THE @evolving.ai FORMAT (from the user's reference recording), per slide:
  - Portrait 1080x1350 (4:5 — IG's tallest feed ratio, what evolving.ai uses)
  - Black background
  - TOP: brand lockup line (round logo + @genzcapital), right-aligned like
    evolving.ai's @handle in the corner
  - HEADLINE near the top: leading "- " dash + Title Case, Anton, ONE word in
    neon green (#39FF14) — that's the Gen Z Capital twist on evolving.ai's
    all-white headline
  - BODY: 2-4 sentence news summary, Inter, light gray-white
  - BOTTOM ~58%: the media zone — a real image/clip of the thing, center-cropped
    to FILL the frame (the user's "clips must fill frame" rule)
  - optional live-counter chip overlaid on the media (evolving.ai's hook gimmick)
  - carousel dots + "follow" chip along the bottom edge
  - Cover slide = oversized hook headline + SWIPE cue
  - Final slide = CTA ("Follow for free", benefit pills) — evolving.ai's
    newsletter-promo end card, rebranded.

GEN Z CAPITAL UNIQUE LAYER (what makes it ours, not a clone):
  - neon green (#39FF14) accent word in every headline + neon divider rule
  - Anton condensed headline font (evolving.ai uses a softer sans)
  - dark cinematic gradient on the media zone
  - "no fluff, no hype" CTA copy + "AI skill you just learned" recap slide

Run:  python publisher/carousel_format.py
Optional sample topic:  python publisher/carousel_format.py --topic openai
"""

import argparse
import glob
import json
import os
import re
import shutil
import subprocess

from PIL import Image, ImageDraw, ImageFont, ImageFilter

try:                                    # source-company logo fetcher (free)
    from publisher.source_logo import logo_for_brands
except Exception:                       # support running as a loose script too
    try:
        from source_logo import logo_for_brands
    except Exception:
        logo_for_brands = None

# ---------------------------------------------------------------------------
# Paths & brand constants
# ---------------------------------------------------------------------------
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONTS = os.path.join(ROOT, "fonts")
ANTON = os.path.join(FONTS, "Anton-Regular.ttf")
INTER = os.path.join(FONTS, "Inter-Regular.ttf")
INTER_BOLD = os.path.join(FONTS, "Inter-Bold.ttf")
# Headline/body font — @evolving.ai uses Arial/Helvetica Bold (a classic
# grotesque), NOT a condensed face. We match it exactly with Arial Bold.
# Locally we bundle the system Arial; in the cloud (Ubuntu) we fall back to
# Liberation Sans Bold, the metric-identical Arial clone preinstalled there.
ARCHIVO = os.path.join(FONTS, "ArchivoBlack-Regular.ttf")


def _first_existing(paths, default):
    for p in paths:
        if p and os.path.exists(p):
            return p
    return default


ARIAL_BOLD = _first_existing([
    os.path.join(FONTS, "Arial-Bold.ttf"),
    "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
], ANTON)
ARIAL_REG = _first_existing([
    os.path.join(FONTS, "Arial-Regular.ttf"),
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
], INTER)

# Exact @evolving.ai match: Arial Bold for the headline, Arial for body text.
HEADLINE_FONT = ARIAL_BOLD
BODY_FONT = ARIAL_REG
BODY_BOLD_FONT = ARIAL_BOLD
LOGO = os.path.join(ROOT, "logo.png")
if not os.path.exists(LOGO):
    LOGO = os.path.join(ROOT, "example_image.png")
# High-res transparent "GenZ CAPITAL" wordmark — used full-quality (scaled DOWN
# only) so it stays crystal clear; this is the logo the user wants on the cover.
WORDMARK = os.path.join(ROOT, "assets", "genz_logo_wordmark.png")

W, H = 1080, 1350           # 4:5 portrait — the evolving.ai feed ratio
WHITE = (245, 246, 248)
NEON = (57, 255, 20)        # #39FF14 — Gen Z Capital signature
GRAY = (176, 180, 188)
DIM = (120, 124, 132)
BLACK = (8, 9, 11)
HANDLE = "@genzcapital"     # change to your real handle if different

MARGIN = 70                 # left/right safe margin
MEDIA_TOP_FRAC = 0.42       # media zone starts ~42% down -> fills bottom 58%


# ---------------------------------------------------------------------------
# Font / text helpers
# ---------------------------------------------------------------------------
def font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def tw(draw, s, f):
    b = draw.textbbox((0, 0), s, font=f)
    return b[2] - b[0]


def wrap(draw, s, f, max_w):
    words, lines, cur = s.split(), [], ""
    for w_ in words:
        test = (cur + " " + w_).strip()
        if tw(draw, test, f) <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w_
    if cur:
        lines.append(cur)
    return lines or [s]


def fit_anton(draw, lines, max_w, start, min_):
    """Largest Anton size where every (already-wrapped) line fits max_w."""
    size = start
    while size > min_:
        f = font(ANTON, size)
        if all(tw(draw, ln, f) <= max_w for ln in lines):
            return f, size
        size -= 3
    return font(ANTON, min_), min_


def find_ffmpeg():
    for pat in ("node_modules/@ffmpeg-installer/*/ffmpeg*",
                "node_modules/ffmpeg-static/ffmpeg*"):
        for p in glob.glob(os.path.join(ROOT, pat)):
            return p
    return "ffmpeg"


def find_media(keywords):
    """Find a local image/video whose path contains any keyword (skip logos).
    NEVER matches inside assets/carousels — those are OUR RENDERED OUTPUTS
    (a keyword like 'anthropic' once matched the deck's own cover PNG, putting
    a text-covered slide inside the next slide's media card)."""
    pats = ["assets/images/**/*.png", "assets/images/**/*.jpg",
            "reels/assets/**/*.jpg",
            "reels/assets/**/*.png", "renders/*.png", "renders/*.jpg"]
    for pat in pats:
        for p in glob.glob(os.path.join(ROOT, pat), recursive=True):
            low = p.lower()
            if any(k in low for k in keywords) and "logo" not in low \
                    and "slide_" not in low:
                return p
    return None


# ---------------------------------------------------------------------------
# Background / media helpers
# ---------------------------------------------------------------------------
def cover_fill(src, w, h):
    """Center-crop to FILL w x h — never letterbox (the 'fill the frame' rule)."""
    src = src.convert("RGB")
    sr, tr = src.width / src.height, w / h
    if sr > tr:
        nh, nw = h, int(src.width * (h / src.height))
    else:
        nw, nh = w, int(src.height * (w / src.width))
    src = src.resize((nw, nh), Image.LANCZOS)
    x0, y0 = (nw - w) // 2, (nh - h) // 2
    return src.crop((x0, y0, x0 + w, y0 + h))


def cinematic_media(seed_top, seed_bot, glow=NEON, w=W, h=H):
    """Painted cinematic placeholder for the media zone when no real clip exists:
    dark vertical gradient + faint perspective grid + a soft neon glow."""
    bg = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(bg)
    for y in range(h):
        t = y / h
        c = tuple(int(seed_top[i] * (1 - t) + seed_bot[i] * t) for i in range(3))
        d.line([(0, y), (w, y)], fill=c)
    grid = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grid)
    horizon = int(h * 0.30)
    for i in range(-12, 13):
        gd.line([(w // 2 + i * 16, horizon), (w // 2 + i * 120, h)],
                fill=(glow[0], glow[1], glow[2], 32), width=2)
    for j in range(1, 14):
        yy = horizon + int((h - horizon) * (j / 14) ** 1.7)
        gd.line([(0, yy), (w, yy)], fill=(glow[0], glow[1], glow[2], 26), width=2)
    grid = grid.filter(ImageFilter.GaussianBlur(1))
    bg = Image.alpha_composite(bg.convert("RGBA"), grid).convert("RGB")
    return bg


def near_black_bg():
    """@evolving.ai content-slide canvas (measured off their real slides):
    near-black (#060606-#0B0B0B, NOT pure black) with barely-visible dark-gray
    diagonal slash shapes in the top-left and bottom-right corners."""
    img = Image.new("RGB", (W, H), (8, 8, 9))
    sl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sl)
    for i in range(3):
        off = i * 90
        sd.polygon([(-60 + off, -200), (140 + off, -200),
                    (-260 + off, 560), (-460 + off, 560)],
                   fill=(26, 26, 28, 255))
        sd.polygon([(W + 60 - off, H + 200), (W - 140 - off, H + 200),
                    (W + 260 - off, H - 560), (W + 460 - off, H - 560)],
                   fill=(26, 26, 28, 255))
    return Image.alpha_composite(img.convert("RGBA"), sl).convert("RGB")


def media_card(keywords, top, bottom, grad=((20, 26, 34), (5, 6, 9))):
    """The @evolving.ai MEDIA CARD: the slide's image in a large rounded-corner
    card (measured: ~28px side margins, ~28px corner radius) instead of a
    full-bleed bottom zone. Returns (card_img, x, y)."""
    pad = 28
    cw, ch = W - pad * 2, bottom - top
    src = find_media(keywords) if keywords else None
    if src:
        card = cover_fill(Image.open(src), cw, ch)
    else:
        card = cinematic_media(grad[0], grad[1], w=cw, h=ch)
    mask = Image.new("L", (cw, ch), 0)
    ImageDraw.Draw(mask).rounded_rectangle([(0, 0), (cw - 1, ch - 1)],
                                           radius=28, fill=255)
    card.putalpha(mask)
    return card, pad, top


def media_zone(keywords, grad=((20, 26, 34), (5, 6, 9))):
    """Return a 1080 x (media height) image to sit in the bottom of the slide."""
    mh = H - int(H * MEDIA_TOP_FRAC)
    src = find_media(keywords) if keywords else None
    if src:
        img = cover_fill(Image.open(src), W, mh)
    else:
        img = cinematic_media(grad[0], grad[1], w=W, h=mh)
    # subtle top fade so the headline area blends into pure black
    fade = Image.new("RGBA", (W, mh), (0, 0, 0, 0))
    fd = ImageDraw.Draw(fade)
    fh = int(mh * 0.22)
    for y in range(fh):
        a = int(255 * (1 - y / fh))
        fd.line([(0, y), (W, y)], fill=(*BLACK, a))
    return Image.alpha_composite(img.convert("RGBA"), fade).convert("RGB")


# ---------------------------------------------------------------------------
# Shared chrome: brand line, dots, follow chip, counter
# ---------------------------------------------------------------------------
def paste_logo(img, x, y, size):
    if not os.path.exists(LOGO):
        return False
    try:
        lg = Image.open(LOGO).convert("RGBA")
        b = lg.split()[3].getbbox()
        if b:
            lg = lg.crop(b)
        lg = lg.resize((size, size), Image.LANCZOS)
        badge = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        ImageDraw.Draw(badge).ellipse([0, 0, size - 1, size - 1], fill=(18, 20, 24, 255))
        img.paste(badge, (x, y), badge)
        img.paste(lg, (x, y), lg)
        return True
    except Exception:
        return False


def paste_wordmark_center(img, target_w, center_y):
    """Paste the high-res GenZ CAPITAL wordmark, horizontally centered, scaled
    DOWN to target_w (never up) so it stays crystal clear. center_y is the
    vertical center of where the logo should sit. Returns (top, height) or None.
    """
    path = WORDMARK if os.path.exists(WORDMARK) else None
    if not path:
        return None
    lg = Image.open(path).convert("RGBA")
    b = lg.split()[3].getbbox()      # trim transparent padding for tight fit
    if b:
        lg = lg.crop(b)
    # scale DOWN only — downscaling stays sharp, upscaling would blur it
    tw_ = min(target_w, lg.width)
    th_ = int(lg.height * (tw_ / lg.width))
    lg = lg.resize((tw_, th_), Image.LANCZOS)
    x = (W - tw_) // 2
    y = int(center_y - th_ / 2)
    img.paste(lg, (x, y), lg)
    return y, th_


def _brands_from_beat(beat, spec=None):
    """Detected source brands for a slide: explicit beat['source_brands'] wins,
    else spec-level, else parse the topic with topic_keywords()."""
    if beat.get("source_brands"):
        return beat["source_brands"]
    if spec and spec.get("source_brands"):
        return spec["source_brands"]
    topic = (spec or {}).get("topic", "") if spec else ""
    try:
        from publisher.carousel_image_pipeline import topic_keywords
    except Exception:
        try:
            from carousel_image_pipeline import topic_keywords
        except Exception:
            return []
    return topic_keywords(topic, beat.get("headline", "")).get("brands", [])


def paste_source_logo(img, brands, topic="", *, x=None, y=None, size=92,
                      chip=True):
    """Composite the SOURCE COMPANY's logo (Claude/OpenAI/...) onto a slide —
    the @evolving.ai move where every slide carries the story's company logo.
    Logo is auto-fetched+cached by source_logo.logo_for_brands. On near-black
    slides the logo sits on a soft rounded WHITE chip so dark marks stay
    visible. Returns (w, h) of the placed chip, or None if no logo resolved."""
    if logo_for_brands is None:
        return None
    path = logo_for_brands(brands, topic)
    if not path or not os.path.exists(path):
        return None
    try:
        lg = Image.open(path).convert("RGBA")
        b = lg.split()[3].getbbox()           # trim transparent padding
        if b:
            lg = lg.crop(b)
        # fit the logo inside the chip with padding
        inner = int(size * 0.74)
        lw = inner
        lh = int(lg.height * (inner / lg.width))
        if lh > inner:
            lh = inner
            lw = int(lg.width * (inner / lg.height))
        lg = lg.resize((lw, lh), Image.LANCZOS)
        if x is None:
            x = MARGIN
        if y is None:
            y = 40
        if chip:
            badge = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            ImageDraw.Draw(badge).rounded_rectangle(
                [0, 0, size - 1, size - 1], radius=int(size * 0.26),
                fill=(248, 248, 250, 255))
            img.paste(badge, (x, y), badge)
            img.paste(lg, (x + (size - lw) // 2, y + (size - lh) // 2), lg)
            return size, size
        img.paste(lg, (x, y), lg)
        return lw, lh
    except Exception:
        return None


def brand_line_top(img, draw):
    """Top-right header EXACTLY like @evolving.ai (verified 2026-06-13):
    round logo icon + TWO stacked lines — bold white display name on top,
    gray @handle underneath. Right margin ~40px, top ~30px."""
    icon = 56
    name = "Gen Z Capital"
    nf = font(HEADLINE_FONT, 28)
    hf = font(BODY_FONT, 24)
    text_w = max(tw(draw, name, nf), tw(draw, HANDLE, hf))
    y = 34
    bx = W - 40 - (icon + 14 + text_w)
    if not paste_logo(img, bx, y, icon):
        draw.ellipse([bx, y, bx + icon, y + icon], fill=NEON)
    tx = bx + icon + 14
    draw.text((tx, y + 2), name, font=nf, fill=WHITE)
    draw.text((tx, y + 32), HANDLE, font=hf, fill=(160, 160, 160))


def carousel_dots(draw, active, total, y=None):
    y = y if y is not None else H - 58
    n = max(total, 1)
    gap = 24
    width = (n - 1) * gap
    x0 = (W - width) // 2
    for i in range(n):
        cx = x0 + i * gap
        col = NEON if i == active else (70, 74, 82)
        r = 6 if i == active else 5
        draw.ellipse([cx - r, y - r, cx + r, y + r], fill=col)


def follow_chip(draw):
    """Small 'FOLLOW @genzcapital' chip bottom-left, evolving.ai style."""
    f = font(ANTON, 26)
    txt = "FOLLOW " + HANDLE.upper()
    pad = 18
    twd = tw(draw, txt, f)
    x0, y0 = MARGIN, H - 100
    draw.rounded_rectangle([(x0, y0), (x0 + twd + pad * 2, y0 + 44)],
                           radius=22, fill=(22, 24, 28))
    draw.ellipse([x0 + 14, y0 + 16, x0 + 26, y0 + 28], fill=NEON)
    draw.text((x0 + pad + 18, y0 + 9), txt, font=f, fill=WHITE)


def counter_chip(img, draw, label, value, low=False):
    """evolving.ai's live-counter gimmick overlaid on the media (e.g.
    '2.1M users / in 48 hours'). Sits in the media zone (top by default, or low
    near the bottom when low=True so it can't collide with a cover headline)."""
    if not value:
        return
    vf = font(ANTON, 60)
    lf = font(INTER, 26)
    pad = 26
    # measure real glyph heights so the value never sits on top of the label
    vb = draw.textbbox((0, 0), value, font=vf)
    lb = draw.textbbox((0, 0), label, font=lf)
    vh, lh = vb[3] - vb[1], lb[3] - lb[1]
    vw = vb[2] - vb[0]
    lw = lb[2] - lb[0]
    gap = 14
    box_w = max(vw, lw) + pad * 2
    box_h = pad + vh + gap + lh + pad
    y_top = int(H * MEDIA_TOP_FRAC) + 40
    x, y = MARGIN, (H - 150 - box_h) if low else y_top
    chip = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
    ImageDraw.Draw(chip).rounded_rectangle(
        [(0, 0), (box_w - 1, box_h - 1)], radius=18, fill=(10, 11, 14, 215))
    img.paste(chip, (x, y), chip)
    # subtract each glyph's own top bearing so they sit flush at the target y
    draw.text((x + pad, y + pad - vb[1]), value, font=vf, fill=NEON)
    draw.text((x + pad, y + pad + vh + gap - lb[1]), label, font=lf, fill=GRAY)


# ---------------------------------------------------------------------------
# Headline rendering (leading dash + one neon word)
# ---------------------------------------------------------------------------
def _neon_set(neon_word):
    """Normalize neon_word (str OR list) into a set of <=2 lowercase tokens.
    Enforces the @evolving.ai rule: only the 1-2 key words get the accent."""
    if not neon_word:
        return set()
    if isinstance(neon_word, str):
        words = neon_word.replace(",", " ").split()
    else:
        words = list(neon_word)
    cleaned = [w.strip().lower().strip(".,!") for w in words if w.strip()]
    return set(cleaned[:2])  # max two highlighted words


def draw_dash_headline(draw, text, top, max_w, start, min_, neon_word=None,
                       lead_dash=False, max_h=None, caps=False,
                       font_path=None, line_gap=8, align="left"):
    """Render a heavy headline with 1-2 words painted neon green. Returns y
    below the headline.

    VERIFIED against real @evolving.ai slides (2026-06-13, Threads CDN pulls):
    they use NO leading dash anywhere — covers are ALL-CAPS centered, content
    headlines sentence case left-aligned. lead_dash stays as an opt-in for old
    callers but defaults OFF. align="center" gives the cover look. caps=True
    uppercases. If max_h is given, the font shrinks until the block also fits
    vertically."""
    fp = font_path or HEADLINE_FONT
    text = text.strip()
    if caps:
        text = text.upper()
    display = ("- " + text) if lead_dash else text
    # size down until it fits horizontally AND (if asked) vertically
    size = start
    while size > min_:
        f = font(fp, size)
        lines = wrap(draw, display, f, max_w)
        block_h = (size + line_gap) * len(lines)
        if all(tw(draw, ln, f) <= max_w for ln in lines) and \
           (max_h is None or block_h <= max_h):
            break
        size -= 3
    f = font(fp, size)
    lines = wrap(draw, display, f, max_w)
    neon = _neon_set(neon_word)
    y = top
    lh = size + line_gap
    for ln in lines:
        x = (W - tw(draw, ln, f)) // 2 if align == "center" else MARGIN
        words = ln.split(" ")
        for i, w_ in enumerate(words):
            chunk = w_ + (" " if i < len(words) - 1 else "")
            col = NEON if w_.lower().strip(".,!") in neon else WHITE
            draw.text((x, y), chunk, font=f, fill=col)
            x += tw(draw, chunk, f)
        y += lh
    return y


# ---------------------------------------------------------------------------
# SLIDE BUILDERS
# ---------------------------------------------------------------------------
def base_slide():
    img = Image.new("RGB", (W, H), BLACK).convert("RGBA")
    return img, ImageDraw.Draw(img)


def _center_brand_bar(draw, y, label="GEN Z CAPITAL"):
    """@evolving.ai's thin centered '——— BRAND ———' rule above the headline."""
    f = font(HEADLINE_FONT, 26)
    label = label.upper()
    lw = tw(draw, label, f)
    cx = W // 2
    draw.text((cx - lw // 2, y), label, font=f, fill=WHITE)
    rule_y = y + 16
    gap = lw // 2 + 24
    draw.rectangle([(MARGIN, rule_y - 1), (cx - gap, rule_y + 2)], fill=NEON)
    draw.rectangle([(cx + gap, rule_y - 1), (W - MARGIN, rule_y + 2)], fill=NEON)
    return y + 44


def cover_slide(out, beat, idx, total, spec=None):
    """Slide 1 — @evolving.ai cover: FULL-BLEED image, big ALL-CAPS headline
    anchored at the BOTTOM over a dark gradient, centered brand bar above it,
    SWIPE FOR MORE + dots at the very bottom. The SOURCE company logo
    (Claude/OpenAI/...) sits top-left over the hero, @evolving.ai style."""
    img, draw = base_slide()

    # 1) FULL-BLEED hero image edge-to-edge (their look), not a bottom band.
    src = find_media(beat.get("media_keywords")) if beat.get("media_keywords") \
        else None
    if src:
        hero = cover_fill(Image.open(src), W, H)
    else:
        grad = beat.get("grad", ((24, 28, 36), (4, 5, 8)))
        hero = cinematic_media(grad[0], grad[1], w=W, h=H)
    img.paste(hero.convert("RGBA"), (0, 0))

    draw = ImageDraw.Draw(img)

    # No top-right logo/handle on the cover (user removed it) — the brand shows
    # only via the centered wordmark on the green bar below.

    # LAYOUT RULE (user, brand spec): TOP 60% = image, BOTTOM 40% = text.
    # The black text band is PINNED at the 60% line and text flows DOWNWARD —
    # so a long headline shrinks to fit the bottom 40% and can NEVER creep up
    # and cover the image's subject. (Old code anchored to the bottom and grew
    # up, which ate the image on long headlines — the bug the user hit.)
    SPLIT = int(H * 0.60)            # the 60/40 divide
    band_top = SPLIT                # fully-black text area starts here
    fp = HEADLINE_FONT

    # The brand bar + logo sit just below the split; headline below that; the
    # subheadline/SWIPE sits near the bottom. Reserve those fixed slots and fit
    # the headline into whatever vertical space is left in the bottom 40%.
    logo_w = int(W * 0.18)
    bar_y = band_top + 70                       # brand bar just under the split
    head_top0 = bar_y + 46                       # headline starts under the bar
    swipe_y = H - 120                            # bottom slot (sub / SWIPE)
    head_avail = (swipe_y - 30) - head_top0      # vertical room for the headline

    # measure the headline so the whole block fits the available bottom space
    txt = beat["headline"].strip().upper()
    size = 92                       # their measured headline size @1080
    while size > 48:
        f = font(fp, size)
        lines = wrap(draw, txt, f, W - MARGIN * 2)
        block_h = (size + 6) * len(lines)
        if all(tw(draw, ln, f) <= W - MARGIN * 2 for ln in lines) and \
           block_h <= head_avail:
            break
        size -= 3
    f = font(fp, size)
    lines = wrap(draw, txt, f, W - MARGIN * 2)
    block_h = (size + 6) * len(lines)
    # center the headline block within its available slot
    head_top = head_top0 + max(0, (head_avail - block_h) // 2)

    # BLACK text area that BLENDS into the image: a fade ramp turns the image
    # fully black by the split line, then solid black below — soft seam, but the
    # ramp stays ABOVE the 60% line so it never darkens the image's subject.
    fade_h = 180
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    fade_start = band_top - fade_h
    for y in range(fade_start, band_top):
        a = int(255 * ((y - fade_start) / fade_h) ** 1.4)
        od.line([(0, y), (W, y)], fill=(*BLACK, a))
    od.rectangle([(0, band_top), (W, H)], fill=(*BLACK, 255))
    img = Image.alpha_composite(img, overlay)
    draw = ImageDraw.Draw(img)

    placed = paste_wordmark_center(img, target_w=logo_w, center_y=bar_y)
    draw = ImageDraw.Draw(img)
    half_gap = (logo_w // 2) + 26 if placed else 90
    draw.rectangle([(MARGIN, bar_y - 1), (W // 2 - half_gap, bar_y + 2)],
                   fill=NEON)
    draw.rectangle([(W // 2 + half_gap, bar_y - 1), (W - MARGIN, bar_y + 2)],
                   fill=NEON)

    draw_dash_headline(draw, beat["headline"], top=head_top,
                       max_w=W - MARGIN * 2, start=size, min_=size,
                       neon_word=beat.get("neon_word"), caps=True, line_gap=6,
                       align="center")

    # 5) Bottom slot — @evolving.ai puts EITHER a small ALL-CAPS subheadline
    #    (2 lines, ~28% of headline size, centered) OR "SWIPE FOR MORE" here,
    #    never both. No carousel dots on their covers.
    sub = beat.get("subheadline", "").strip()
    if sub:
        sf = font(HEADLINE_FONT, 28)
        y = swipe_y
        for ln in wrap(draw, sub.upper(), sf, W - MARGIN * 2)[:2]:
            draw.text(((W - tw(draw, ln, sf)) // 2, y), ln, font=sf,
                      fill=WHITE)
            y += 38
    else:
        sf = font(HEADLINE_FONT, 30)
        s = "SWIPE FOR MORE  →"
        draw.text(((W - tw(draw, s, sf)) // 2, swipe_y + 20), s, font=sf,
                  fill=WHITE)

    # 6) SOURCE-company logo (Claude/OpenAI/...) top-left over the hero — the
    #    @evolving.ai signature. Auto-fetched + cached; absent if unresolved.
    paste_source_logo(img, _brands_from_beat(beat, spec),
                      topic=(spec or {}).get("topic", ""),
                      x=MARGIN, y=46, size=104)

    img.convert("RGB").save(out)
    return out


def content_slide(out, beat, idx, total, spec=None):
    """Middle slides — the verified @evolving.ai content layout: near-black
    canvas with corner slashes, two-line handle header top-right, small
    sentence-case headline LEFT-aligned, then a big rounded MEDIA CARD filling
    the rest. Our teaching twist: kicker + a short body line stay (their format
    has no body, but our niche must teach), so the card starts a bit lower.
    No follow chip, no dots — they use neither."""
    img = near_black_bg().convert("RGBA")
    draw = ImageDraw.Draw(img)
    brand_line_top(img, draw)

    top = 132
    if beat.get("kicker"):
        kf = font(HEADLINE_FONT, 28)
        draw.text((MARGIN, top), beat["kicker"].upper(), font=kf, fill=NEON)
        top += 46

    # headline — sentence case as written in the spec, left at the margin,
    # their measured size class (~36px @1080); ours slightly larger since it
    # carries the teaching point. Tight ~1.15 leading.
    end_y = draw_dash_headline(draw, beat["headline"], top=top,
                               max_w=W - MARGIN * 2, start=46, min_=36,
                               neon_word=beat.get("neon_word"), line_gap=8,
                               max_h=220)

    # body — short, gray, Arial; sits between headline and the media card
    body = beat.get("body", "")
    y = end_y + 14
    if body:
        bf = font(BODY_FONT, 33)
        for ln in wrap(draw, body, bf, W - MARGIN * 2):
            draw.text((MARGIN, y), ln, font=bf, fill=GRAY)
            y += 44

    # the rounded media card fills everything below the text block
    card, cx, cy = media_card(beat.get("media_keywords"), top=y + 28,
                              bottom=H - 40,
                              grad=beat.get("grad", ((18, 22, 30), (4, 5, 8))))
    img.paste(card, (cx, cy), card)
    draw = ImageDraw.Draw(img)

    # SOURCE-company logo in the media card's top-left corner (@evolving.ai
    # puts the story's company logo on every slide). Auto-fetched + cached.
    paste_source_logo(img, _brands_from_beat(beat, spec),
                      topic=(spec or {}).get("topic", ""),
                      x=cx + 22, y=cy + 22, size=84)

    counter_chip(img, draw, beat.get("counter_label", ""),
                 beat.get("counter_value", ""))
    img.convert("RGB").save(out)
    return out


def recap_slide(out, beat, idx, total):
    """The Gen Z Capital 'what you just learned' slide — the knowledge-base twist
    that separates us from a pure news clone. Numbered takeaways, no media."""
    img = near_black_bg().convert("RGBA")
    draw = ImageDraw.Draw(img)
    brand_line_top(img, draw)

    title = beat.get("headline", "The takeaways")
    draw_dash_headline(draw, title, top=170, max_w=W - MARGIN * 2,
                       start=72, min_=52, neon_word=beat.get("neon_word"))

    points = beat.get("points", [])
    nf = font(HEADLINE_FONT, 44)
    pf = font(BODY_BOLD_FONT, 36)
    y = 360
    for i, p in enumerate(points, start=1):
        draw.ellipse([MARGIN, y, MARGIN + 52, y + 52], fill=NEON)
        nw = tw(draw, str(i), nf)
        draw.text((MARGIN + 26 - nw // 2, y + 1), str(i), font=nf, fill=BLACK)
        ty = y
        for ln in wrap(draw, p, pf, W - MARGIN * 2 - 80):
            draw.text((MARGIN + 78, ty), ln, font=pf, fill=WHITE)
            ty += 48
        y = max(ty, y + 70) + 24

    img.convert("RGB").save(out)
    return out


def cta_slide(out, beat, idx, total):
    """Final slide — evolving.ai newsletter-promo end card, rebranded.
    'Follow for free' + benefit pills + Gen Z Capital lockup."""
    img = near_black_bg().convert("RGBA")
    draw = ImageDraw.Draw(img)

    # centered brand lockup
    icon = 96
    paste_logo(img, (W - icon) // 2, 150, icon)
    draw = ImageDraw.Draw(img)
    wf = font(HEADLINE_FONT, 52)
    wm = "GEN Z CAPITAL"
    draw.text(((W - tw(draw, wm, wf)) // 2, 150 + icon + 18), wm, font=wf, fill=WHITE)

    head = beat.get("headline", "Follow for free").upper()
    size = 88
    while size > 56 and tw(draw, head, font(HEADLINE_FONT, size)) > W - MARGIN * 2:
        size -= 3
    hf = font(HEADLINE_FONT, size)
    draw.text(((W - tw(draw, head, hf)) // 2, 440), head, font=hf, fill=NEON)

    pills = beat.get("pills", [
        "DAILY AI TOOLS & HOW-TOS",
        "LEARN TO USE AI YOURSELF",
        "NO FLUFF. NO HYPE.",
    ])
    pf = font(HEADLINE_FONT, 36)
    y = 600
    for p in pills:
        pw = tw(draw, p, pf)
        x0 = (W - pw) // 2 - 44
        x1 = (W + pw) // 2 + 44
        draw.rounded_rectangle([(x0, y), (x1, y + pf.size + 26)],
                               radius=24, fill=(24, 26, 32))
        draw.ellipse([x0 + 18, y + (pf.size + 26) // 2 - 7,
                      x0 + 32, y + (pf.size + 26) // 2 + 7], fill=NEON)
        draw.text(((W - pw) // 2 + 18, y + 12), p, font=pf, fill=WHITE)
        y += pf.size + 26 + 24

    cf = font(BODY_FONT, 40)
    s = "Tap follow + save this post"
    draw.text(((W - tw(draw, s, cf)) // 2, H - 220), s, font=cf, fill=GRAY)
    bf = font(HEADLINE_FONT, 58)
    s2 = HANDLE.upper()
    draw.text(((W - tw(draw, s2, bf)) // 2, H - 160), s2, font=bf, fill=NEON)

    img.convert("RGB").save(out)
    return out


def video_slide(out_dir, idx, total, video_path, beat):
    """Copy a real reel/clip in as a video slide + extract its IG cover frame
    (cover gets the same headline overlay so the carousel reads consistently)."""
    dst = os.path.join(out_dir, f"slide_{idx + 1}_video.mp4")
    cover = os.path.join(out_dir, f"slide_{idx + 1}_cover.png")
    if video_path and os.path.exists(video_path):
        shutil.copy(video_path, dst)
        try:
            subprocess.run(
                [find_ffmpeg(), "-y", "-ss", "1", "-i", video_path, "-frames:v", "1",
                 "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}",
                 cover], check=True, capture_output=True)
        except Exception as e:
            print(f"  (cover extract failed: {e})")
    # overlay headline + chrome on the cover so it matches the deck
    if os.path.exists(cover):
        img = cover_fill(Image.open(cover), W, H).convert("RGBA")
    else:
        img = media_zone(beat.get("media_keywords")).convert("RGBA")
        img = cover_fill(img, W, H).convert("RGBA")
    # The reel usually carries its OWN burned-in text, so by default we only add
    # the brand chrome + a small play cue (no big headline -> no double-texting).
    # Pass beat["overlay_headline"]=True to force the dash headline back on.
    overlay_headline = beat.get("overlay_headline", False)
    draw = ImageDraw.Draw(img)
    if overlay_headline:
        grad = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        gd = ImageDraw.Draw(grad)
        for y in range(int(H * 0.46)):
            a = int(210 * (1 - y / (H * 0.46)))
            gd.line([(0, y), (W, y)], fill=(*BLACK, a))
        img = Image.alpha_composite(img, grad)
        draw = ImageDraw.Draw(img)
        draw_dash_headline(draw, beat.get("headline", "WATCH THIS"), top=150,
                           max_w=W - MARGIN * 2, start=92, min_=56,
                           max_h=int(H * 0.30),
                           neon_word=beat.get("neon_word"))
    # small play pill bottom-center so it reads as a video without hiding the clip
    pf = font(HEADLINE_FONT, 36)
    pill = "▶  PLAY"
    pw = tw(draw, pill, pf)
    px = (W - pw) // 2 - 28
    py = H - 170
    draw.rounded_rectangle([(px, py), (px + pw + 56, py + 56)],
                           radius=28, fill=(10, 11, 14, 210))
    draw.text(((W - pw) // 2, py + 10), pill, font=pf, fill=NEON)
    brand_line_top(img, draw)
    img.convert("RGB").save(cover)
    return dst, cover


def caption_slide(out, caption, idx, total, bg_keywords=None):
    """The CAPTION card — shows the Instagram caption text itself, over a
    BLURRED full-bleed background (the user wants a blurry bg, not flat black,
    so the text reads on top of it). Reuses the cover's hero image when given,
    else a painted cinematic bg. Auto-shrinks the body so it all fits the frame.
    """
    img, _ = base_slide()

    # 1) BLURRED full-bleed background (the cover image if we have one).
    src = find_media(bg_keywords) if bg_keywords else None
    if src:
        bg = cover_fill(Image.open(src), W, H)
    else:
        bg = cinematic_media((24, 28, 36), (4, 5, 8), w=W, h=H)
    bg = bg.filter(ImageFilter.GaussianBlur(22))
    img.paste(bg.convert("RGBA"), (0, 0))

    # 2) Dark overlay over the whole frame so white text always stays readable.
    veil = Image.new("RGBA", (W, H), (0, 0, 0, 165))
    img = Image.alpha_composite(img, veil)
    draw = ImageDraw.Draw(img)

    # small label up top so it's obvious this is the caption card
    lf = font(HEADLINE_FONT, 30)
    label = "CAPTION"
    draw.text((MARGIN, 70), label, font=lf, fill=NEON)
    draw.line([(MARGIN, 118), (W - MARGIN, 118)], fill=(120, 124, 132), width=2)

    top = 160
    bottom = H - 80
    max_w = W - MARGIN * 2
    avail_h = bottom - top

    # Arial has no emoji glyphs -> they render as empty boxes on the slide.
    # Strip non-BMP chars (emoji) for the ON-SLIDE text only; caption.txt
    # keeps them for the real Instagram post.
    clean = "".join(ch for ch in caption if ord(ch) <= 0xFFFF)
    paras = [p.strip() for p in clean.split("\n\n")]

    # find the largest body size where the whole caption fits the available box
    size = 38
    while size >= 20:
        bf = font(BODY_FONT, size)
        line_h = int(size * 1.34)
        para_gap = int(size * 0.7)
        total_h, lines_by_para = 0, []
        for p in paras:
            lines = [] if not p.strip() else wrap(draw, p, bf, max_w)
            lines_by_para.append(lines)
            total_h += max(len(lines), 1) * line_h + para_gap
        if total_h <= avail_h:
            break
        size -= 2

    y = top
    for lines in lines_by_para:
        if not lines:
            y += para_gap
            continue
        # color the hashtag paragraph neon, the rest white
        is_tags = lines and lines[0].lstrip().startswith("#")
        for ln in lines:
            draw.text((MARGIN, y), ln, font=bf,
                      fill=NEON if is_tags else WHITE)
            y += line_h
        y += para_gap

    img.convert("RGB").save(out)
    return out


# ---------------------------------------------------------------------------
# ORCHESTRATOR
# ---------------------------------------------------------------------------
def slugify(s):
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:40] or "carousel"


def build_caption(spec):
    """Build the Instagram CAPTION in @evolving.ai's structure:
        HOOK line  ->  short value summary  ->  the content / the prompt  ->
        CTA ('Follow @genzcapital ...')  ->  hashtags.

    Uses spec['caption'] overrides if present, else derives sensible text from
    the slides. Returns the full caption string.
    """
    cap = spec.get("caption", {}) or {}
    slides = spec.get("slides", [])
    topic = spec.get("topic", "")

    # HOOK — first line that stops the scroll
    hook = cap.get("hook") or topic

    # VALUE — one/two sentence summary (default: the problem + the fix slides)
    summary = cap.get("summary")
    if not summary:
        probs = [b.get("body", "") for b in slides
                 if b.get("type") == "content" and b.get("body")]
        summary = probs[0] if probs else ""

    # CONTENT — the meat (e.g. the actual prompt). Pull the slide flagged
    # caption_body, else the longest body (usually the prompt-reveal slide).
    content = cap.get("content")
    if not content:
        flagged = [b.get("body", "") for b in slides if b.get("caption_body")]
        if flagged:
            content = flagged[0]
        else:
            bodies = [b.get("body", "") for b in slides if b.get("body")]
            content = max(bodies, key=len) if bodies else ""

    cta = cap.get("cta") or (
        f"Save this + follow {HANDLE} for daily AI tools & how-tos.")

    tags = cap.get("hashtags") or [
        "#AItools", "#artificialintelligence", "#AI", "#promptengineering",
        "#ChatGPT", "#aitips", "#futuretech", "#genzcapital",
    ]
    hashtags = " ".join(tags)

    parts = [hook]
    if summary:
        parts.append(summary)
    if content:
        parts.append(content)
    parts.append(cta)
    parts.append(hashtags)
    return "\n\n".join(p.strip() for p in parts if p and p.strip())


def build_carousel(spec, out_dir=None):
    """Render a full carousel from a spec dict.

    spec = {
      "topic": "...",                # used for the output folder name
      "slides": [ {beat}, ... ]      # each beat has "type" + fields
    }
    Beat types: cover | content | recap | cta | video
    Returns the ordered list of output file paths.
    """
    topic = spec.get("topic", "carousel")
    out_dir = out_dir or os.path.join(ROOT, "assets", "carousels", slugify(topic))
    os.makedirs(out_dir, exist_ok=True)
    slides = spec["slides"]
    # the deck includes a final CAPTION card, so it counts toward the dot total
    total = len(slides) + 1
    paths = []
    print(f"Building carousel '{topic}'  ({total} slides) -> {os.path.relpath(out_dir, ROOT)}\n")

    for i, beat in enumerate(slides):
        t = beat.get("type", "content")
        out = os.path.join(out_dir, f"slide_{i + 1}_{t}.png")
        if t == "cover":
            p = cover_slide(out, beat, i, total, spec=spec)
        elif t == "recap":
            p = recap_slide(out, beat, i, total)
        elif t == "cta":
            p = cta_slide(out, beat, i, total)
        elif t == "video":
            vid, cover = video_slide(out_dir, i, total, beat.get("video_path"), beat)
            print(f"  [{i+1}] VIDEO   -> {os.path.relpath(vid, ROOT)}  (cover {os.path.basename(cover)})")
            paths.append(vid)
            continue
        else:
            p = content_slide(out, beat, i, total, spec=spec)
        print(f"  [{i+1}] {t:<8}-> {os.path.relpath(p, ROOT)}")
        paths.append(p)

    # write the Instagram caption (@evolving.ai-style) next to the slides
    caption = build_caption(spec)
    caption_path = os.path.join(out_dir, "caption.txt")
    with open(caption_path, "w", encoding="utf-8") as fh:
        fh.write(caption)

    # render a final caption card (blurred bg = the cover image) that shows
    # the caption text itself
    cover_kw = slides[0].get("media_keywords") if slides else None
    cap_out = os.path.join(out_dir, f"slide_{len(slides) + 1}_caption.png")
    caption_slide(cap_out, caption, len(slides), total, bg_keywords=cover_kw)
    print(f"  [{len(slides) + 1}] caption -> {os.path.relpath(cap_out, ROOT)}")
    paths.append(cap_out)

    # write a manifest the publisher can read to know slide order + caption
    manifest = os.path.join(out_dir, "carousel.json")
    with open(manifest, "w", encoding="utf-8") as fh:
        json.dump({"topic": topic,
                   "slides": [os.path.basename(p) for p in paths],
                   "caption": caption}, fh, indent=2)
    print(f"\nManifest: {os.path.relpath(manifest, ROOT)}")
    print(f"Caption:  {os.path.relpath(caption_path, ROOT)}")
    print("Upload these in order as one Instagram carousel.")
    return paths


# ---------------------------------------------------------------------------
# SAMPLE SPEC — a real, ready-to-post Gen Z Capital x evolving.ai carousel
# ---------------------------------------------------------------------------
def sample_spec():
    """A complete 8-slide sample carousel in the evolving.ai format, on a topic
    that fits Gen Z Capital's 'teach a usable AI skill' rule."""
    real_reel = None
    for cand in ("renders/claude-builds-3d-worlds-image-blaster-by.mp4",
                 "reels/assets/clips"):
        p = os.path.join(ROOT, cand)
        if os.path.isfile(p):
            real_reel = p
            break
    return {
        "topic": "5 AI tools that replace a whole team",
        "slides": [
            {
                "type": "cover",
                "kicker": "AI TOOLS — JUNE 2026",
                "headline": "5 AI Tools That Replace A Whole Team",
                "neon_word": "Replace",
                "counter_value": "5 tools",
                "counter_label": "one-person company stack",
                "media_keywords": ["office", "reference", "resized"],
            },
            {
                "type": "content",
                "kicker": "THE SHIFT",
                "headline": "One Person Now Runs A Real Company",
                "neon_word": "One",
                "body": "Solo founders are shipping products that used to need 10 hires. "
                        "The leverage isn't money anymore — it's knowing which AI tools to chain together.",
                "media_keywords": ["office", "reference"],
            },
            {
                "type": "content",
                "kicker": "1. RESEARCH",
                "headline": "Claude — Your Strategy Department",
                "neon_word": "Claude",
                "body": "Drop in a goal, get a full plan, competitor breakdown and next steps. "
                        "Treat it like a co-founder, not a search box.",
                "media_keywords": ["claude", "reference"],
            },
            {
                "type": "video",
                "headline": "Build It While You Describe It",
                "neon_word": "Describe",
                "video_path": real_reel,
                "media_keywords": ["3d", "world", "render"],
            },
            {
                "type": "content",
                "kicker": "3. CONTENT",
                "headline": "Automate Your Whole Posting Pipeline",
                "neon_word": "Automate",
                "body": "Research, write, design and schedule run on autopilot. "
                        "You approve — the system does the work. This account is built that way.",
                "media_keywords": ["reference", "office"],
            },
            {
                "type": "content",
                "kicker": "WHY IT MATTERS",
                "headline": "The Barrier To Building Just Hit Zero",
                "neon_word": "Zero",
                "body": "Skills you'd have paid a team for are now one prompt away. "
                        "The only limit left is knowing what to ask for.",
                "media_keywords": ["office", "reference"],
            },
            {
                "type": "recap",
                "headline": "What You Just Learned",
                "neon_word": "Learned",
                "points": [
                    "Use Claude as your strategy + research department.",
                    "Describe what you want built — let AI write the code.",
                    "Automate the content pipeline end to end.",
                    "One skilled person + AI now beats a small team.",
                ],
            },
            {
                "type": "cta",
                "headline": "Follow For Free",
                "pills": [
                    "DAILY AI TOOLS & HOW-TOS",
                    "LEARN TO USE AI YOURSELF",
                    "NO FLUFF. NO HYPE.",
                ],
            },
        ],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", help="path to a JSON spec file")
    ap.add_argument("--topic", help="override the output folder topic")
    args = ap.parse_args()

    if args.spec:
        with open(args.spec, encoding="utf-8") as fh:
            spec = json.load(fh)
    else:
        spec = sample_spec()
    if args.topic:
        spec["topic"] = args.topic

    build_carousel(spec)


if __name__ == "__main__":
    main()
