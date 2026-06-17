"""
sample_carousel_3dworld.py — HAND-BUILT SAMPLE (no API calls, no cost).

Builds ONE @evolving.ai-style Instagram carousel for the topic
"Claude builds entire 3D worlds from one prompt", rebranded to Gen Z Capital.

Canvas: 1080x1920 (full vertical) so the existing 1080x1920 3D-world reel
drops in as a video slide with ZERO cropping.

Slides produced into  assets/carousels/3dworld_sample/ :
  slide_1_hook.png   — cinematic bg + Claude logo + Anton headline + GEN Z CAPITAL + SWIPE FOR MORE
  slide_2_story1.png — story beat 1 (image bg + headline + body)
  slide_3_video.mp4  — the REAL 3D-world reel, copied in as the carousel video slide
  slide_3_cover.png  — the video's own cover frame (what IG shows as the thumbnail)
  slide_4_story2.png — story beat 2
  slide_5_cta.png    — Gen Z Capital "Join the #1 AI How-To page" CTA (link in bio)

This mirrors publisher/post_generator.py ImageProcessor styling (Anton headline,
neon #39FF14, center-cropped photo zone, logo + neon dividers) but at 1080x1920.

Run:  python publisher/sample_carousel_3dworld.py
Needs: Pillow (already in requirements.txt). ffmpeg is auto-located from node_modules.
Background images: uses any local images it can find; otherwise paints a cinematic
dark gradient so the sample still renders without network access.
"""

import glob
import os
import shutil
import subprocess

from PIL import Image, ImageDraw, ImageFont, ImageFilter

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "assets", "carousels", "3dworld_sample")
FONTS = os.path.join(ROOT, "fonts")
ANTON = os.path.join(FONTS, "Anton-Regular.ttf")
INTER = os.path.join(FONTS, "Inter-Regular.ttf")
LOGO = os.path.join(ROOT, "logo.png")
VIDEO = os.path.join(ROOT, "renders", "claude-builds-3d-worlds-image-blaster-by.mp4")

W, H = 1080, 1920
WHITE = (255, 255, 255)
NEON = (57, 255, 20)        # #39FF14
GRAY = (160, 160, 160)
YELLOW = (255, 196, 0)      # evolving.ai uses a warm gold accent on the CTA slide

os.makedirs(OUT_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# Font + ffmpeg helpers
# ---------------------------------------------------------------------------
def font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def find_ffmpeg():
    for p in glob.glob(os.path.join(ROOT, "node_modules", "@ffmpeg-installer", "*", "ffmpeg*")):
        return p
    for p in glob.glob(os.path.join(ROOT, "node_modules", "ffmpeg-static", "ffmpeg*")):
        return p
    return "ffmpeg"


def text_w(draw, s, f):
    b = draw.textbbox((0, 0), s, font=f)
    return b[2] - b[0]


def wrap(draw, s, f, max_w):
    words, lines, cur = s.split(), [], ""
    for w_ in words:
        test = (cur + " " + w_).strip()
        if text_w(draw, test, f) <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w_
    if cur:
        lines.append(cur)
    return lines or [s]


def fit_font(draw, lines, max_w, start, min_, path):
    """Largest Anton size where every line fits max_w."""
    size = start
    while size > min_:
        f = font(path, size)
        if all(text_w(draw, ln, f) <= max_w for ln in lines):
            return f, size
        size -= 4
    return font(path, min_), min_


# ---------------------------------------------------------------------------
# Background helpers
# ---------------------------------------------------------------------------
def cinematic_bg(seed_color_top, seed_color_bot):
    """Paint a dark cinematic vertical gradient as a fallback background."""
    bg = Image.new("RGB", (W, H), seed_color_bot)
    d = ImageDraw.Draw(bg)
    for y in range(H):
        t = y / H
        r = int(seed_color_top[0] * (1 - t) + seed_color_bot[0] * t)
        g = int(seed_color_top[1] * (1 - t) + seed_color_bot[1] * t)
        b = int(seed_color_top[2] * (1 - t) + seed_color_bot[2] * t)
        d.line([(0, y), (W, y)], fill=(r, g, b))
    return bg


def cover_fill(src, w=W, h=H):
    """Center-crop src to completely fill w x h (no bars) — your 'fill the frame' rule."""
    src = src.convert("RGB")
    sr, tr = src.width / src.height, w / h
    if sr > tr:
        nh, nw = h, int(src.width * (h / src.height))
    else:
        nw, nh = w, int(src.height * (w / src.width))
    src = src.resize((nw, nh), Image.LANCZOS)
    return src.crop(((nw - w) // 2, (nh - h) // 2, (nw - w) // 2 + w, (nh - h) // 2 + h))


def darken_bottom(img, frac=0.55):
    """Black gradient rising from the bottom so text is always readable."""
    grad = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    start = int(H * (1 - frac))
    for y in range(start, H):
        a = int(235 * ((y - start) / (H - start)))
        gd.line([(0, y), (W, y)], fill=(0, 0, 0, a))
    return Image.alpha_composite(img.convert("RGBA"), grad).convert("RGB")


def find_bg(*keywords):
    """Find a local image whose path contains any keyword; else None."""
    pats = ["assets/images/**/*.png", "assets/images/**/*.jpg",
            "reels/assets/**/*.jpg", "*.png"]
    for pat in pats:
        for p in glob.glob(os.path.join(ROOT, pat), recursive=True):
            low = p.lower()
            if any(k in low for k in keywords) and "logo" not in low:
                return p
    return None


# ---------------------------------------------------------------------------
# Branding bits shared by image slides
# ---------------------------------------------------------------------------
def draw_logo_block(img, draw, label="GEN Z CAPITAL", y=70):
    """Small brand lockup with neon dividers — like the EVOLVING|AI watermark line."""
    f = font(ANTON, 30)
    lw = text_w(draw, label, f)
    lx = (W - lw) // 2
    line_y = y + 20
    draw.rectangle([(80, line_y), (lx - 24, line_y + 3)], fill=NEON)
    draw.rectangle([(lx + lw + 24, line_y), (W - 80, line_y + 3)], fill=NEON)
    # try the real logo above the wordmark
    if os.path.exists(LOGO):
        try:
            lg = Image.open(LOGO).convert("RGBA")
            bbox = lg.split()[3].getbbox()
            if bbox:
                lg = lg.crop(bbox)
            tw = 150
            lg = lg.resize((tw, int(lg.height * tw / lg.width)), Image.LANCZOS)
            img.paste(lg, ((W - tw) // 2, y - lg.height - 6), lg)
        except Exception:
            pass
    draw.text((lx, y), label, font=f, fill=WHITE)


def draw_headline(draw, lines, colors, top, max_w=W - 120, start=120, min_=64,
                  gap=14, tracking=3):
    """Anton uppercase headline, one color per line, centered, with tight tracking."""
    up = [l.upper() for l in lines]
    f, size = fit_font(draw, up, max_w, start, min_, ANTON)
    y = top
    for ln, col in zip(up, colors):
        cw = sum(text_w(draw, ch, f) + tracking for ch in ln) - tracking
        x = (W - cw) // 2
        for ch in ln:
            draw.text((x, y), ch, font=f, fill=col, stroke_width=2, stroke_fill=col)
            x += text_w(draw, ch, f) + tracking
        y += size + gap
    return y


# ---------------------------------------------------------------------------
# SLIDE 1 — HOOK
# ---------------------------------------------------------------------------
def slide_hook():
    bgp = find_bg("office", "resized")  # any dramatic local photo
    if bgp:
        base = cover_fill(Image.open(bgp))
    else:
        base = cinematic_bg((28, 30, 36), (6, 7, 9))
    base = darken_bottom(base, 0.62)
    img = base.convert("RGBA")
    draw = ImageDraw.Draw(img)

    # brand watermark top
    draw_logo_block(img, draw, "GEN Z CAPITAL", y=80)

    # big bold hook at the lower third, evolving.ai style
    headline = ["CLAUDE NOW", "BUILDS ENTIRE 3D", "WORLDS FROM", "ONE PROMPT"]
    colors = [WHITE, NEON, WHITE, WHITE]
    bottom_block_top = H - 720
    draw_headline(draw, headline, colors, bottom_block_top,
                  max_w=W - 100, start=118, min_=72, gap=10)

    # swipe prompt
    f = font(ANTON, 40)
    s = "SWIPE FOR MORE"
    draw.text(((W - text_w(draw, s, f)) // 2, H - 150), s, font=f, fill=WHITE)
    # little neon dot row like a carousel indicator
    for i in range(5):
        cx = W // 2 - 48 + i * 24
        col = NEON if i == 0 else (120, 120, 120)
        draw.ellipse([(cx, H - 80), (cx + 10, H - 70)], fill=col)

    out = os.path.join(OUT_DIR, "slide_1_hook.png")
    img.convert("RGB").save(out)
    return out


# ---------------------------------------------------------------------------
# STORY SLIDES (image + headline + body)
# ---------------------------------------------------------------------------
def story_slide(idx, kicker, headline_lines, head_colors, body, bg_keywords,
                grad_top=(20, 24, 30), grad_bot=(5, 6, 8)):
    bgp = find_bg(*bg_keywords)
    base = cover_fill(Image.open(bgp)) if bgp else cinematic_bg(grad_top, grad_bot)
    base = darken_bottom(base, 0.7)
    img = base.convert("RGBA")
    draw = ImageDraw.Draw(img)

    draw_logo_block(img, draw, "GEN Z CAPITAL", y=80)

    # kicker
    kf = font(ANTON, 38)
    draw.text(((W - text_w(draw, kicker, kf)) // 2, H - 720), kicker,
              font=kf, fill=NEON)

    y = draw_headline(draw, headline_lines, head_colors, H - 650,
                      max_w=W - 110, start=104, min_=58, gap=8)

    # body
    bf = font(INTER, 38)
    y += 24
    for ln in wrap(draw, body, bf, W - 160):
        draw.text(((W - text_w(draw, ln, bf)) // 2, y), ln, font=bf, fill=GRAY)
        y += 52

    out = os.path.join(OUT_DIR, f"slide_{idx}.png")
    img.convert("RGB").save(out)
    return out


# ---------------------------------------------------------------------------
# VIDEO SLIDE — copy the real reel + extract its cover frame
# ---------------------------------------------------------------------------
def video_slide():
    dst = os.path.join(OUT_DIR, "slide_3_video.mp4")
    cover = os.path.join(OUT_DIR, "slide_3_cover.png")
    if os.path.exists(VIDEO):
        shutil.copy(VIDEO, dst)
        ff = find_ffmpeg()
        try:
            subprocess.run([ff, "-y", "-ss", "1", "-i", VIDEO, "-frames:v", "1",
                            "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,"
                                   f"crop={W}:{H}", cover],
                           check=True, capture_output=True)
        except Exception as e:
            print(f"  (cover extract failed: {e})")
    else:
        # placeholder if the reel isn't where we expect
        ph = cinematic_bg((10, 30, 12), (2, 6, 2)).convert("RGBA")
        d = ImageDraw.Draw(ph)
        f = font(ANTON, 64)
        for i, ln in enumerate(["CLAUDE 3D WORLD", "VIDEO HERE"]):
            d.text(((W - text_w(d, ln, f)) // 2, H // 2 - 60 + i * 80), ln,
                   font=f, fill=NEON)
        ph.convert("RGB").save(cover)
    return dst, cover


# ---------------------------------------------------------------------------
# SLIDE 5 — CTA (Gen Z Capital, evolving.ai newsletter-promo layout)
# ---------------------------------------------------------------------------
def slide_cta():
    base = cinematic_bg((18, 20, 26), (4, 5, 7))
    img = base.convert("RGBA")
    draw = ImageDraw.Draw(img)

    draw_logo_block(img, draw, "GEN Z CAPITAL", y=90)

    # headline: "JOIN THE #1 AI HOW-TO PAGE"
    headline = ["JOIN THE #1", "AI HOW-TO PAGE"]
    colors = [WHITE, YELLOW]
    draw_headline(draw, headline, colors, 300, max_w=W - 120,
                  start=120, min_=70, gap=10)

    # benefit pills, like evolving.ai's "+100,000 readers / Always free / ..."
    pills = ["+ DAILY AI TOOLS & HOW-TOS", "ALWAYS FREE",
             "LEARN TO USE AI YOURSELF", "NO FLUFF, NO HYPE",
             "NEVER MISS A THING IN AI"]
    pf = font(ANTON, 46)
    y = 640
    for p in pills:
        pw = text_w(draw, p, pf)
        pad_x, pad_h = 50, 28
        bx0 = (W - pw) // 2 - pad_x
        bx1 = (W + pw) // 2 + pad_x
        draw.rounded_rectangle([(bx0, y), (bx1, y + pf.size + pad_h)],
                               radius=22, fill=(32, 34, 40))
        draw.text(((W - pw) // 2, y + pad_h // 2), p, font=pf, fill=WHITE)
        y += pf.size + pad_h + 26

    # footer CTA
    f1 = font(INTER, 44)
    s1 = "Link in bio"
    draw.text(((W - text_w(draw, s1, f1)) // 2, H - 280), s1, font=f1, fill=WHITE)
    f2 = font(ANTON, 92)
    s2 = "FOLLOW FOR FREE"
    for ln in wrap(draw, s2, f2, W - 100):
        draw.text(((W - text_w(draw, ln, f2)) // 2, H - 210), ln, font=f2, fill=YELLOW)

    out = os.path.join(OUT_DIR, "slide_5_cta.png")
    img.convert("RGB").save(out)
    return out


# ---------------------------------------------------------------------------
def main():
    print(f"Building sample carousel into: {OUT_DIR}\n")
    s1 = slide_hook()
    print(f"  [1] hook    -> {os.path.relpath(s1, ROOT)}")

    s2 = story_slide(
        "2_story1",
        kicker="THE SHIFT",
        headline_lines=["YOU DESCRIBE IT.", "CLAUDE CODES THE", "WHOLE 3D SCENE."],
        head_colors=[WHITE, WHITE, NEON],
        body="Type one prompt — Claude writes the code, builds the geometry, "
             "lights it, and ships a running 3D world. No engine skills needed.",
        bg_keywords=["office", "resized", "reference"],
    )
    print(f"  [2] story1  -> {os.path.relpath(s2, ROOT)}")

    vid, cover = video_slide()
    print(f"  [3] VIDEO   -> {os.path.relpath(vid, ROOT)}")
    print(f"      cover   -> {os.path.relpath(cover, ROOT)}")

    s4 = story_slide(
        "4_story2",
        kicker="WHY IT MATTERS",
        headline_lines=["THE BARRIER TO", "BUILDING JUST", "HIT ZERO."],
        head_colors=[WHITE, WHITE, NEON],
        body="Game devs, designers and founders can prototype playable 3D in "
             "minutes. The only limit left is the idea you can describe.",
        bg_keywords=["office", "resized", "reference"],
    )
    print(f"  [4] story2  -> {os.path.relpath(s4, ROOT)}")

    s5 = slide_cta()
    print(f"  [5] cta     -> {os.path.relpath(s5, ROOT)}")

    print("\nDone. Carousel slide order for Instagram:")
    print("  1) slide_1_hook.png")
    print("  2) slide_2_story1.png")
    print("  3) slide_3_video.mp4   (cover: slide_3_cover.png)")
    print("  4) slide_4_story2.png")
    print("  5) slide_5_cta.png")


if __name__ == "__main__":
    main()
