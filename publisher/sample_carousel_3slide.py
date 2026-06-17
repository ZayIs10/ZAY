"""
sample_carousel_3slide.py — HAND-BUILT 3-SLIDE SAMPLE (no API calls, no cost).

Topic: "Claude builds entire 3D worlds from one prompt" (Gen Z Capital).
Layout copies the @watcher.guru viral-thumbnail format for slides 1 & 3:
  - full-bleed cinematic collage image
  - centered brand lockup (round logo icon + @handle) on a thin green divider line
  - big bold condensed headline at the bottom, white with some words in green

Slides into  assets/carousels/3dworld_3slide/ :
  slide_1_intro.png  — watcher.guru-style intro (bg + logo line + headline)
  slide_2_video.mp4  — the REAL 3D-world reel (copied in)  + slide_2_cover.png
  slide_3_end.png    — watcher.guru-style end/CTA (same format as slide 1)

Backgrounds: if assets/carousels/3dworld_3slide/bg_slide1.png (and bg_slide3.png)
exist, those are used (drop your DALL-E images there). Otherwise a cinematic
digital-grid placeholder is painted so you can see the finished layout immediately.

Run:  python publisher/sample_carousel_3slide.py
"""

import glob
import os
import shutil
import subprocess

from PIL import Image, ImageDraw, ImageFont, ImageFilter

# ---------------------------------------------------------------------------
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "assets", "carousels", "3dworld_3slide")
FONTS = os.path.join(ROOT, "fonts")
ANTON = os.path.join(FONTS, "Anton-Regular.ttf")
LOGO = os.path.join(ROOT, "logo.png")
VIDEO = os.path.join(ROOT, "renders", "claude-builds-3d-worlds-image-blaster-by.mp4")

W, H = 1080, 1350          # 4:5 portrait, exactly like the watcher.guru reference
WHITE = (245, 245, 245)
GREEN = (40, 220, 70)      # watcher.guru's bright green
HANDLE = "@genzcapital"    # <-- change to your real handle

os.makedirs(OUT_DIR, exist_ok=True)


# ---- helpers ---------------------------------------------------------------
def font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def tw(draw, s, f):
    b = draw.textbbox((0, 0), s, font=f)
    return b[2] - b[0]


def find_ffmpeg():
    for p in glob.glob(os.path.join(ROOT, "node_modules", "@ffmpeg-installer", "*", "ffmpeg*")):
        return p
    return "ffmpeg"


def cover_fill(src, w, h):
    src = src.convert("RGB")
    sr, tr = src.width / src.height, w / h
    if sr > tr:
        nh, nw = h, int(src.width * (h / src.height))
    else:
        nw, nh = w, int(src.height * (w / src.width))
    src = src.resize((nw, nh), Image.LANCZOS)
    return src.crop(((nw - w) // 2, (nh - h) // 2, (nw - w) // 2 + w, (nh - h) // 2 + h))


def placeholder_bg(variant=1):
    """Cinematic digital-grid + glow placeholder, painted so the layout is visible
    even before the real DALL-E image is dropped in."""
    # vertical gradient: warm top -> deep blue bottom (matches the reference mood)
    top = (38, 30, 20) if variant == 1 else (30, 22, 38)
    mid = (20, 30, 55)
    bot = (4, 6, 12)
    bg = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(bg)
    for y in range(H):
        t = y / H
        if t < 0.4:
            u = t / 0.4
            c = tuple(int(top[i] * (1 - u) + mid[i] * u) for i in range(3))
        else:
            u = (t - 0.4) / 0.6
            c = tuple(int(mid[i] * (1 - u) + bot[i] * u) for i in range(3))
        d.line([(0, y), (W, y)], fill=c)

    # glowing perspective grid (the "digital world" feel)
    grid = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grid)
    horizon = int(H * 0.45)
    for i in range(-10, 21):
        x = W // 2 + i * 90
        gd.line([(W // 2 + i * 14, horizon), (x, H)], fill=(60, 180, 220, 90), width=2)
    for j in range(1, 16):
        y = horizon + int((H - horizon) * (j / 16) ** 1.7)
        gd.line([(0, y), (W, y)], fill=(60, 180, 220, 70), width=2)
    grid = grid.filter(ImageFilter.GaussianBlur(1))
    bg = Image.alpha_composite(bg.convert("RGBA"), grid).convert("RGB")

    # central glow orb
    orb = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(orb)
    cx, cy, r = W // 2, int(H * 0.34), 240
    for rr in range(r, 0, -4):
        a = int(70 * (1 - rr / r))
        od.ellipse([cx - rr, cy - rr, cx + rr, cy + rr],
                   fill=(80, 200, 255, a))
    orb = orb.filter(ImageFilter.GaussianBlur(8))
    bg = Image.alpha_composite(bg.convert("RGBA"), orb).convert("RGB")
    return bg


def darken_bottom(img, frac=0.42, strength=240):
    grad = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    start = int(H * (1 - frac))
    for y in range(start, H):
        a = int(strength * ((y - start) / (H - start)))
        gd.line([(0, y), (W, y)], fill=(0, 0, 0, a))
    return Image.alpha_composite(img.convert("RGBA"), grad).convert("RGB")


def draw_brand_line(img, draw, y):
    """Round logo icon + @handle centered on a thin green divider line —
    the exact watcher.guru lockup."""
    hf = font(ANTON, 34)
    handle_w = tw(draw, HANDLE, hf)
    icon = 46
    gap = 14
    block_w = icon + gap + handle_w
    bx = (W - block_w) // 2

    line_y = y + icon // 2
    # green divider lines on both sides
    draw.rectangle([(70, line_y - 1), (bx - 24, line_y + 2)], fill=GREEN)
    draw.rectangle([(bx + block_w + 24, line_y - 1), (W - 70, line_y + 2)], fill=GREEN)

    # round logo icon
    if os.path.exists(LOGO):
        try:
            lg = Image.open(LOGO).convert("RGBA")
            b = lg.split()[3].getbbox()
            if b:
                lg = lg.crop(b)
            lg = lg.resize((icon, icon), Image.LANCZOS)
            # circular green badge behind the logo
            badge = Image.new("RGBA", (icon, icon), (0, 0, 0, 0))
            ImageDraw.Draw(badge).ellipse([0, 0, icon - 1, icon - 1], fill=GREEN)
            img.paste(badge, (bx, y), badge)
            img.paste(lg, (bx, y), lg)
        except Exception:
            draw.ellipse([bx, y, bx + icon, y + icon], fill=GREEN)
    else:
        draw.ellipse([bx, y, bx + icon, y + icon], fill=GREEN)

    # handle text
    draw.text((bx + icon + gap, y + 4), HANDLE, font=hf, fill=WHITE)


def draw_headline(img, draw, words_colored, baseline_bottom):
    """words_colored: list of lines, each line a list of (word, color).
    Bold condensed Anton, slight stroke, bottom-anchored — watcher.guru look.
    Returns the y of the TOP of the headline block (so callers can place the
    brand line safely above it)."""
    # pick a size that fits the widest line
    size = 150
    while size > 70:
        f = font(ANTON, size)
        ok = True
        for line in words_colored:
            line_w = sum(tw(draw, w + " ", f) for w, _ in line)
            if line_w > W - 70:
                ok = False
                break
        if ok:
            break
        size -= 4
    f = font(ANTON, size)
    line_h = size + 6
    total_h = line_h * len(words_colored)
    top = baseline_bottom - total_h
    y = top
    for line in words_colored:
        line_w = sum(tw(draw, w + " ", f) for w, _ in line)
        x = (W - line_w) // 2
        for w, col in line:
            draw.text((x, y), w, font=f, fill=col, stroke_width=3, stroke_fill=(0, 0, 0))
            x += tw(draw, w + " ", f)
        y += line_h
    return top


# ---- slides ----------------------------------------------------------------
def watcher_slide(out_name, bg_file, headline):
    bgp = os.path.join(OUT_DIR, bg_file)
    if os.path.exists(bgp):
        base = cover_fill(Image.open(bgp), W, H)
    else:
        base = placeholder_bg(variant=1 if "1" in out_name else 2)
    base = darken_bottom(base, frac=0.40, strength=245)
    img = base.convert("RGBA")
    draw = ImageDraw.Draw(img)

    # headline anchored near the bottom; brand line sits safely above it
    head_top = draw_headline(img, draw, headline, baseline_bottom=H - 70)
    draw_brand_line(img, draw, y=head_top - 70)

    out = os.path.join(OUT_DIR, out_name)
    img.convert("RGB").save(out)
    return out


def video_slide():
    dst = os.path.join(OUT_DIR, "slide_2_video.mp4")
    cover = os.path.join(OUT_DIR, "slide_2_cover.png")
    if os.path.exists(VIDEO):
        shutil.copy(VIDEO, dst)
        try:
            subprocess.run([find_ffmpeg(), "-y", "-ss", "1", "-i", VIDEO,
                            "-frames:v", "1",
                            "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,"
                                   f"crop={W}:{H}", cover],
                           check=True, capture_output=True)
        except Exception as e:
            print(f"  (cover extract failed: {e})")
    return dst, cover


def main():
    print(f"Building 3-slide sample into: {OUT_DIR}\n")

    s1 = watcher_slide(
        "slide_1_intro.png", "bg_slide1.png",
        headline=[[("THE", WHITE), ("NEW", GREEN), ("WAY", GREEN), ("TO", WHITE)],
                  [("BUILD A", WHITE), ("3D", GREEN), ("WORLD", GREEN)]],
    )
    print(f"  [1] intro -> {os.path.relpath(s1, ROOT)}")

    vid, cover = video_slide()
    print(f"  [2] VIDEO -> {os.path.relpath(vid, ROOT)}  (cover {os.path.basename(cover)})")

    s3 = watcher_slide(
        "slide_3_end.png", "bg_slide3.png",
        headline=[[("YOU JUST", WHITE), ("DESCRIBE", GREEN), ("IT", GREEN)],
                  [("CLAUDE", WHITE), ("BUILDS", GREEN), ("IT", GREEN)]],
    )
    print(f"  [3] end   -> {os.path.relpath(s3, ROOT)}")

    print("\nDone. Drop your DALL-E images at:")
    print(f"  {os.path.relpath(os.path.join(OUT_DIR, 'bg_slide1.png'), ROOT)}")
    print(f"  {os.path.relpath(os.path.join(OUT_DIR, 'bg_slide3.png'), ROOT)}")
    print("  then re-run to see them with the real backgrounds.")


if __name__ == "__main__":
    main()
