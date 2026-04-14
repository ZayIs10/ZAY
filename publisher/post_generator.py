"""
Gen Z Automation - Post Generator
Daily script: reads one "Ready" topic from Google Sheets,
generates a caption + cinematic image, overlays text with Pillow,
uploads to ImgBB, publishes to Instagram, and marks the row Published.

Run manually:  python post_generator.py
Run via scheduler: handled by scheduler.py
"""

import base64
import io
import json
import logging
import os
import sys
import time
from datetime import datetime

import gspread
import requests
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials
from openai import OpenAI
from PIL import Image, ImageDraw, ImageFont
from usage_guard import UsageGuard, UsageLimitError

load_dotenv()


# ---------------------------------------------------------------------------
# DALL-E prompt builder — deterministic, wealth-specific
# ---------------------------------------------------------------------------

# Maps topic keyword sets → subject key in config["wealth_image_subjects"]
TOPIC_SUBJECT_MAP = [
    ({"ai", "automation", "robot", "gpt", "machine",
     "algorithm", "software", "code", "developer"}, "ai_tech"),
    ({"server", "gpu", "data center", "cloud",
     "compute", "infrastructure"}, "server_room"),
    ({"trading", "trader", "bloomberg", "stock market",
     "wall street", "options", "futures"}, "trading"),
    ({"chart", "portfolio", "invest", "stocks",
     "crypto", "bitcoin", "market"}, "trading_screens"),
    ({"cash", "money", "dollar", "currency", "bills", "wealth",
     "rich", "millionaire", "million"}, "money_wealth"),
    ({"portfolio", "returns", "gains", "profit",
     "income", "passive income"}, "portfolio"),
    ({"founder", "entrepreneur", "startup", "ceo", "office",
     "hustle", "grind", "build"}, "founder_office"),
    ({"car", "lambo", "ferrari", "luxury",
     "lifestyle", "flex", "supercar"}, "luxury_car"),
    ({"jet", "private jet", "travel", "fly", "aviation"}, "private_jet"),
    ({"yacht", "boat", "ocean", "sea", "sailing", "beach"}, "yacht"),
]


_DALLE_PROMPT_SYSTEM = (
    "You are a cinematic art director for Gen Z Capital, a dark luxury wealth/AI/automation brand. "
    "Your job: given a post topic and key points, write ONE specific DALL-E 3 image prompt. "
    "The image must be PHOTO-REALISTIC and cinematic — NOT a chart, NOT a generic office. "
    "Rules:\n"
    "- Scene must be SPECIFIC to the topic. Visualize the concept creatively and uniquely.\n"
    "- Dark, moody, cinematic. Single dramatic light source. Deep shadows.\n"
    "- No people's faces visible (silhouettes or backs only).\n"
    "- No text, no watermarks, no UI elements, no phone screens with visible apps.\n"
    "- Shot on RED Komodo 6K, anamorphic lens, f/1.8, ISO 3200 film grain, Kodachrome grade.\n"
    "- Output: ONE paragraph, max 60 words. No intro, no explanation. Just the prompt."
)


def build_dalle_prompt(topic: str, key_points: str, config: dict,
                       client=None, enriched_context: str = "") -> str:
    """Generate a topic-specific cinematic DALL-E prompt using GPT-4o.
    Falls back to static keyword map if client is not available."""
    if client is not None:
        try:
            user_msg = (
                f"Topic: {topic}\n"
                f"Key Points: {key_points}\n"
                f"Context: {enriched_context[:300] if enriched_context else 'none'}\n\n"
                "Write the DALL-E 3 image prompt."
            )
            resp = client.chat.completions.create(
                model=config.get("openai", {}).get("model", "gpt-4o"),
                temperature=0.9,
                max_tokens=120,
                messages=[
                    {"role": "system", "content": _DALLE_PROMPT_SYSTEM},
                    {"role": "user", "content": user_msg},
                ],
            )
            prompt = resp.choices[0].message.content.strip()
            logging.info(f"AI image prompt: {prompt[:100]}...")
            return prompt
        except Exception as e:
            logging.warning(f"AI prompt generation failed ({e}), using static fallback.")

    # Static fallback
    subjects = config.get("wealth_image_subjects", {})
    modifiers = config.get("dalle_prompt_modifiers", {})
    combined = (topic + " " + key_points).lower()
    subject_key = "founder_office"
    for keywords, key in TOPIC_SUBJECT_MAP:
        if any(kw in combined for kw in keywords):
            subject_key = key
            break
    subject = subjects.get(subject_key, "A cinematic dark luxury scene, dramatic lighting")
    style = modifiers.get("style", "cinematic, dramatic lighting")
    lighting = modifiers.get("lighting", "single-source dramatic lighting")
    forbidden = modifiers.get("forbidden", "no text, no watermarks")
    return f"{subject}. {style}. {lighting}. {forbidden}."


# ---------------------------------------------------------------------------
# Config & Logging
# ---------------------------------------------------------------------------

def load_config(config_path: str = None) -> dict:
    if config_path is None:
        config_path = os.path.join(os.path.dirname(
            __file__), "..", "research", "research_config.json")
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    # Override config with environment variables (keeps secrets out of JSON)
    if os.getenv("OPENAI_API_KEY"):
        config["openai"]["api_key"] = os.getenv("OPENAI_API_KEY")
    if os.getenv("INSTAGRAM_ACCESS_TOKEN"):
        config["instagram"]["access_token"] = os.getenv(
            "INSTAGRAM_ACCESS_TOKEN")
    if os.getenv("INSTAGRAM_IG_USER_ID"):
        config["instagram"]["ig_user_id"] = os.getenv("INSTAGRAM_IG_USER_ID")
    if os.getenv("GOOGLE_SHEET_ID"):
        config["google_sheets"]["spreadsheet_id"] = os.getenv(
            "GOOGLE_SHEET_ID")
    if os.getenv("IMGBB_API_KEY"):
        config["image_hosting"]["imgbb_api_key"] = os.getenv("IMGBB_API_KEY")

    return config


def setup_logging(config: dict) -> None:
    logs_dir = os.path.join(config["output_dir"], "logs")
    os.makedirs(logs_dir, exist_ok=True)
    log_path = os.path.join(logs_dir, "post_generator_log.txt")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


# ---------------------------------------------------------------------------
# Step 1: Read next "Ready" topic from Google Sheets
# ---------------------------------------------------------------------------

class SheetsReader:
    SCOPES = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]

    def __init__(self, config: dict):
        self.cfg = config["google_sheets"]
        creds_path = os.path.join(
            config["output_dir"], self.cfg["credentials_file"])
        creds = Credentials.from_service_account_file(
            creds_path, scopes=self.SCOPES)
        self.gc = gspread.authorize(creds)
        sh = self.gc.open_by_key(self.cfg["spreadsheet_id"])
        self.ws = sh.worksheet(self.cfg["sheet_name"])

    def get_next_ready_row(self) -> dict | None:
        """Returns the first row where Status='Ready', or None."""
        all_rows = self.ws.get_all_records()
        for i, row in enumerate(all_rows, start=2):  # row 2 = first data row
            if str(row.get("Status", "")).strip().lower() == "ready":
                row["_row_index"] = i
                return row
        return None

    def mark_in_progress(self, row_index: int) -> None:
        status_col = self._col_index("Status")
        self.ws.update_cell(row_index, status_col, "In Progress")

    def write_image_url(self, row_index: int, image_url: str) -> None:
        """Write the generated image URL to the 'Image File' column for preview."""
        try:
            img_col = self._col_index("Image File")
            self.ws.update_cell(row_index, img_col, image_url)
        except (ValueError, Exception):
            pass  # Column may not exist — non-critical

    def mark_published(self, row_index: int, post_url: str, post_id: str) -> None:
        status_col = self._col_index("Status")
        date_col = self._col_index("Published Date")
        url_col = self._col_index("Post URL")
        post_id_col = self._col_index("Instagram Post ID")

        today = datetime.utcnow().strftime("%Y-%m-%d")
        self.ws.update_cell(row_index, status_col,  "Published")
        self.ws.update_cell(row_index, date_col,    today)
        self.ws.update_cell(row_index, url_col,     post_url)
        self.ws.update_cell(row_index, post_id_col, post_id)

    def _col_index(self, header: str) -> int:
        headers = self.ws.row_values(1)
        return headers.index(header) + 1  # gspread is 1-indexed


# ---------------------------------------------------------------------------
# Step 2: Generate caption + image prompt via GPT-4o
# ---------------------------------------------------------------------------

class CaptionGenerator:
    def __init__(self, config: dict, client: OpenAI, usage_guard: UsageGuard | None = None):
        self.client = client
        self.model = config["openai"]["model"]
        self.temperature = config["openai"]["temperature"]
        self.usage_guard = usage_guard

    def generate(self, row: dict) -> dict:
        topic = row.get("Topic", "")
        key_points = row.get("Key Points / Slide Content", "") or row.get("Key Points", "")
        brand_tone = row.get("Brand Tone", "Gen Z cinematic dark aesthetic. Bold, direct, no fluff. Neon green energy.")
        enriched_context = row.get("Enriched Context", "")

        context_block = ""
        if enriched_context:
            context_block = (
                f"\nREAL-WORLD CONTEXT (use specific stats/facts to make content more credible):\n"
                f"{enriched_context}\n"
            )

        user_msg = (
            f"Topic: {topic}\n"
            f"Key Points: {key_points}\n"
            f"Brand Tone: {brand_tone}\n"
            f"{context_block}\n"
            "Generate post_caption: High-impact Instagram caption. Either ultra-short (5 words max) "
            "OR a punchy 3-4 line caption (Line 1: setup, Line 2: key number or power word, "
            "Line 3: consequence). No emojis. Reference real numbers from context when available.\n\n"
            "Return ONLY valid JSON with key: post_caption"
        )

        resp = self.client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a Gen Z social media copywriter for an AI/automation brand. "
                        "Bold, minimal, punchy. Direct, serious, high-stakes. No emojis. "
                        "Output valid JSON with exactly one key: post_caption."
                    ),
                },
                {"role": "user", "content": user_msg},
            ],
        )
        if self.usage_guard:
            self.usage_guard.register_chat_usage(getattr(resp, "usage", None))

        result = json.loads(resp.choices[0].message.content)
        safe_preview = result['post_caption'][:80].encode(
            'ascii', 'replace').decode('ascii')
        logging.info(f"Caption: {safe_preview}...")
        return result


# ---------------------------------------------------------------------------
# Step 3: Generate image with DALL-E 3
# ---------------------------------------------------------------------------

class ImageGenerator:
    def __init__(self, config: dict, client: OpenAI, usage_guard: UsageGuard | None = None):
        self.client = client
        self.cfg = config["openai"]
        self.usage_guard = usage_guard

    def generate(self, prompt: str, quality: str = None) -> bytes:
        """Returns raw image bytes. Prompt should be fully constructed by build_dalle_prompt()."""
        selected_quality = quality or self.cfg.get("dalle_quality", "hd")
        if self.usage_guard:
            self.usage_guard.register_image_generation(selected_quality)

        resp = self.client.images.generate(
            model=self.cfg.get("dalle_model", "dall-e-3"),
            prompt=prompt,
            size=self.cfg.get("dalle_size", "1024x1024"),
            quality=selected_quality,
            style="vivid",
            n=1,
            response_format="url",
        )

        image_url = resp.data[0].url
        logging.info(f"DALL-E image generated: {image_url[:60]}...")
        img_resp = requests.get(image_url, timeout=60)
        img_resp.raise_for_status()
        return img_resp.content


# ---------------------------------------------------------------------------
# Step 4: Pillow — resize + black overlay + text
# ---------------------------------------------------------------------------

def hex_to_rgb(hex_color: str) -> tuple:
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


class ImageProcessor:
    TARGET_W = 1080
    TARGET_H = 1350
    PHOTO_H = 810   # top 60% — cinematic image zone
    BLACK_H = 540   # bottom 40% — guaranteed pixel-perfect black text zone
    GRAD_H = 120   # gradient fade height at boundary

    def __init__(self, config: dict):
        self.cfg = config["image"]
        self.output_dir = config["output_dir"]
        self.headline_font_fallbacks = [
            "C:/Windows/Fonts/impact.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
            "C:/Windows/Fonts/bahnschrift.ttf",
        ]
        self.body_font_fallbacks = [
            "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/segoeui.ttf",
        ]

    def _build_canvas(self, image_bytes: bytes) -> Image.Image:
        """Compose the two-zone canvas: 810px photo (center-cropped) + 540px solid black."""
        src = Image.open(io.BytesIO(image_bytes)).convert("RGBA")

        # Center-crop source to fill 1080×810 (no distortion)
        src_ratio = src.width / src.height
        target_ratio = self.TARGET_W / self.PHOTO_H
        if src_ratio > target_ratio:
            # wider than needed — fit height, crop width
            new_h = self.PHOTO_H
            new_w = int(src.width * (self.PHOTO_H / src.height))
        else:
            # taller than needed — fit width, crop height
            new_w = self.TARGET_W
            new_h = int(src.height * (self.TARGET_W / src.width))
        src = src.resize((new_w, new_h), Image.LANCZOS)
        left = (new_w - self.TARGET_W) // 2
        top = (new_h - self.PHOTO_H) // 2
        photo = src.crop((left, top, left + self.TARGET_W, top + self.PHOTO_H))

        # Apply gradient fade at bottom of photo zone (transparent → black)
        grad_overlay = Image.new(
            "RGBA", (self.TARGET_W, self.PHOTO_H), (0, 0, 0, 0))
        grad_draw = ImageDraw.Draw(grad_overlay)
        grad_start = self.PHOTO_H - self.GRAD_H
        for y in range(self.GRAD_H):
            alpha = int(255 * (y / self.GRAD_H))
            grad_draw.rectangle(
                [(0, grad_start + y), (self.TARGET_W, grad_start + y + 1)],
                fill=(0, 0, 0, alpha),
            )
        photo = Image.alpha_composite(photo, grad_overlay)

        # Build final canvas: black background, paste photo on top
        canvas = Image.new(
            "RGBA", (self.TARGET_W, self.TARGET_H), (0, 0, 0, 255))
        canvas.paste(photo, (0, 0), photo)
        return canvas.convert("RGB")

    def _draw_logo(self, img: Image.Image, draw: ImageDraw.ImageDraw,
                   logo_path: str, zone_top: int) -> int:
        """Draw logo.png as-is (uses its own alpha channel) + neon dividers.
        Returns y coordinate below the logo."""
        NEON = hex_to_rgb(self.cfg.get("highlight_color", "#39FF14"))
        logo_section_top = zone_top + 18
        logo_bottom_y = logo_section_top

        if logo_path and os.path.exists(logo_path):
            try:
                logo_img = Image.open(logo_path).convert("RGBA")

                # Crop away any fully-transparent padding
                bbox = logo_img.split()[3].getbbox()
                if bbox:
                    logo_img = logo_img.crop(bbox)

                # Scale to target width (240px) preserving aspect ratio
                target_logo_w = 240
                logo_w = target_logo_w
                logo_h = int(logo_img.height * (target_logo_w / logo_img.width))
                logo_img = logo_img.resize((logo_w, logo_h), Image.LANCZOS)
                mask = logo_img.split()[3]

                logo_x = (self.TARGET_W - logo_w) // 2
                logo_y = logo_section_top

                # Neon green divider lines on both sides, vertically centered on logo
                line_y = logo_y + logo_h // 2
                draw.rectangle([(40, line_y), (logo_x - 16, line_y + 2)], fill=NEON)
                draw.rectangle([(logo_x + logo_w + 16, line_y),
                                (self.TARGET_W - 40, line_y + 2)], fill=NEON)

                img.paste(logo_img, (logo_x, logo_y), mask)
                logo_bottom_y = logo_y + logo_h + 16

            except Exception as e:
                logging.warning(f"Logo load failed: {e}")
        else:
            line_y = logo_section_top + 10
            draw.rectangle(
                [(40, line_y), (self.TARGET_W - 40, line_y + 2)], fill=NEON)
            logo_bottom_y = line_y + 20

        return logo_bottom_y

    def _fit_text_to_zone(self, lines: list, zone_top: int, zone_bottom: int,
                          draw: ImageDraw.ImageDraw) -> tuple:
        """Return (headline_font, body_font, headline_size, body_size) that fit all lines."""
        available_px = zone_bottom - zone_top - 24  # 24px bottom margin
        body_size = 24

        for headline_size in [78, 66, 56, 48]:
            hfont = self._load_font(self.cfg.get("headline_font"), headline_size,
                                    self.headline_font_fallbacks)
            bfont = self._load_font(self.cfg.get("body_font"), body_size,
                                    self.body_font_fallbacks)
            total_h = 0
            for i, line in enumerate(lines):
                font = hfont if i < 3 else bfont
                text = line.upper() if i < 3 else line
                max_w = self.TARGET_W - 120 if i < 3 else self.TARGET_W - 80
                if i < 3:
                    wrapped = self._wrap_line_tracking(text, font, draw, max_w)
                else:
                    wrapped = self._wrap_line(text, font, draw, max_w)
                for part in wrapped:
                    bbox = draw.textbbox((0, 0), part, font=font)
                    total_h += (bbox[3] - bbox[1]) + 8
                total_h += 4  # inter-line gap

            if total_h <= available_px:
                return hfont, bfont, headline_size, body_size

        # Fallback: smallest sizes
        hfont = self._load_font(self.cfg.get(
            "headline_font"), 48, self.headline_font_fallbacks)
        bfont = self._load_font(self.cfg.get(
            "body_font"), 20, self.body_font_fallbacks)
        return hfont, bfont, 48, 20

    def process(self, image_bytes: bytes, caption: str, logo_path: str = None) -> str:
        """Two-zone composite: 810px cinematic photo + 540px solid black text zone.
        Returns path to saved JPEG."""
        lines = [l.strip() for l in caption.strip().splitlines() if l.strip()]

        img = self._build_canvas(image_bytes)
        draw = ImageDraw.Draw(img)

        zone_top = self.PHOTO_H  # 810 — start of the black text zone
        logo_bottom_y = self._draw_logo(img, draw, logo_path, zone_top)

        text_color = hex_to_rgb(self.cfg.get("text_color", "#FFFFFF"))
        high_color = hex_to_rgb(self.cfg.get("highlight_color", "#39FF14"))
        gray_color = hex_to_rgb(self.cfg.get("body_color", "#A0A0A0"))

        headline_font, body_font, _, _ = self._fit_text_to_zone(
            lines, logo_bottom_y, self.TARGET_H, draw)

        text_y = logo_bottom_y + 8
        for i, line in enumerate(lines):
            if i < 3:
                font = headline_font
                color = high_color if i == 1 else text_color
                text = line.upper()
                wrapped = self._wrap_line_tracking(
                    text, font, draw, self.TARGET_W - 120)
            else:
                font = body_font
                color = gray_color
                text = line
                wrapped = self._wrap_line(text, font, draw, self.TARGET_W - 80)

            for part in wrapped:
                if text_y + 20 > self.TARGET_H - 20:
                    break
                if i < 3:
                    text_h = self._draw_tracking_text_centered(
                        draw=draw, y=text_y, text=part, font=font,
                        color=color, tracking=2, stroke_width=2)
                else:
                    bbox = draw.textbbox((0, 0), part, font=font)
                    text_w = bbox[2] - bbox[0]
                    text_h = bbox[3] - bbox[1]
                    draw.text(((self.TARGET_W - text_w) // 2, text_y), part,
                              font=font, fill=color)
                text_y += text_h + 8
            text_y += 4

        out_path = os.path.join(
            self.output_dir, f"post_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.jpg")
        img.save(out_path, "JPEG", quality=95)
        logging.info(f"Image saved: {out_path}")
        return out_path

    def process_carousel_slide(self, slide: dict, image_bytes: bytes,
                               slide_num: int, total_slides: int,
                               logo_path: str = None) -> str:
        """Render one carousel slide. Logo on every slide. Returns path to saved JPEG."""
        slide_type = slide.get("slide_type", "content")
        headline = slide.get("headline", "").strip()
        body = slide.get("body", "").strip()
        accent = slide.get("accent_number", "").strip()

        NEON = hex_to_rgb(self.cfg.get("highlight_color", "#39FF14"))
        WHITE = hex_to_rgb(self.cfg.get("text_color", "#FFFFFF"))
        GRAY = hex_to_rgb(self.cfg.get("body_color", "#A0A0A0"))

        img = self._build_canvas(image_bytes)
        draw = ImageDraw.Draw(img)
        zone_top = self.PHOTO_H

        # Logo on every slide — consistent with single image posts
        logo_bottom_y = self._draw_logo(img, draw, logo_path, zone_top)
        text_start = logo_bottom_y + 14
        available_h = self.TARGET_H - text_start - 24  # space below logo

        if slide_type == "cover":
            # Big headline, last word neon green — vertically centered below logo
            font = self._load_font(self.cfg.get("headline_font"), 86,
                                   self.headline_font_fallbacks)
            words = headline.upper().split()
            main_text = " ".join(words[:-1]) if len(words) > 1 else headline.upper()
            accent_text = words[-1] if len(words) > 1 else ""

            # Measure total height first to center it
            total_h = 0
            for part in self._wrap_line_tracking(main_text, font, draw, self.TARGET_W - 80):
                b = draw.textbbox((0, 0), part, font=font)
                total_h += (b[3] - b[1]) + 10
            if accent_text:
                b = draw.textbbox((0, 0), accent_text, font=font)
                total_h += (b[3] - b[1]) + 10

            text_y = text_start + max(0, (available_h - total_h) // 2)
            for part in self._wrap_line_tracking(main_text, font, draw, self.TARGET_W - 80):
                self._draw_tracking_text_centered(draw, text_y, part, font, WHITE, 2, 2)
                b = draw.textbbox((0, 0), part, font=font)
                text_y += (b[3] - b[1]) + 10
            if accent_text:
                self._draw_tracking_text_centered(
                    draw, text_y, accent_text, font, NEON, 2, 2)

        elif slide_type == "stat":
            # Big neon number + white label, centered below logo
            num_font = self._load_font(self.cfg.get("headline_font"), 110,
                                       self.headline_font_fallbacks)
            label_font = self._load_font(self.cfg.get("body_font"), 32,
                                         self.body_font_fallbacks)
            # Measure to center
            total_h = 0
            if accent:
                b = draw.textbbox((0, 0), accent, font=num_font)
                total_h += (b[3] - b[1]) + 16
            if headline:
                b = draw.textbbox((0, 0), headline, font=label_font)
                total_h += (b[3] - b[1])

            text_y = text_start + max(0, (available_h - total_h) // 2)
            if accent:
                self._draw_tracking_text_centered(
                    draw, text_y, accent, num_font, NEON, 1, 2)
                b = draw.textbbox((0, 0), accent, font=num_font)
                text_y += (b[3] - b[1]) + 16
            if headline:
                b = draw.textbbox((0, 0), headline, font=label_font)
                draw.text(((self.TARGET_W - (b[2] - b[0])) // 2, text_y),
                          headline, font=label_font, fill=WHITE)

        elif slide_type == "cta":
            cta_font = self._load_font(self.cfg.get("headline_font"), 72,
                                       self.headline_font_fallbacks)
            cta_text = headline.upper() if headline else "FOLLOW FOR MORE"
            b = draw.textbbox((0, 0), cta_text, font=cta_font)
            cta_y = text_start + max(0, (available_h - (b[3] - b[1])) // 2)
            self._draw_tracking_text_centered(
                draw, cta_y, cta_text, cta_font, NEON, 2, 2)

        else:  # content slides
            font_h = self._load_font(self.cfg.get("headline_font"), 62,
                                     self.headline_font_fallbacks)
            font_b = self._load_font(self.cfg.get("body_font"), 24,
                                     self.body_font_fallbacks)
            text_y = text_start + 10
            if headline:
                for part in self._wrap_line_tracking(
                        headline.upper(), font_h, draw, self.TARGET_W - 100):
                    self._draw_tracking_text_centered(
                        draw, text_y, part, font_h, WHITE, 2, 2)
                    b = draw.textbbox((0, 0), part, font=font_h)
                    text_y += (b[3] - b[1]) + 10
            if body:
                text_y += 14
                for part in self._wrap_line(body, font_b, draw, self.TARGET_W - 100):
                    b = draw.textbbox((0, 0), part, font=font_b)
                    draw.text(((self.TARGET_W - (b[2] - b[0])) // 2, text_y),
                              part, font=font_b, fill=GRAY)
                    text_y += (b[3] - b[1]) + 8

            # Slide counter bottom-right
            counter_font = self._load_font(self.cfg.get("body_font"), 18,
                                           self.body_font_fallbacks)
            counter = f"{slide_num} / {total_slides}"
            cb = draw.textbbox((0, 0), counter, font=counter_font)
            draw.text((self.TARGET_W - (cb[2] - cb[0]) - 30,
                       self.TARGET_H - (cb[3] - cb[1]) - 20),
                      counter, font=counter_font, fill=GRAY)

        out_path = os.path.join(
            self.output_dir,
            f"carousel_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_slide{slide_num:02d}.jpg")
        img.save(out_path, "JPEG", quality=95)
        logging.info(f"Carousel slide {slide_num} saved: {out_path}")
        return out_path

    def _wrap_line(self, text: str, font, draw: ImageDraw.ImageDraw, max_width: int) -> list[str]:
        """Splits text into lines that fit within max_width."""
        words = text.split()
        lines = []
        current = ""
        for word in words:
            test = (current + " " + word).strip()
            bbox = draw.textbbox((0, 0), test, font=font)
            if bbox[2] - bbox[0] <= max_width:
                current = test
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)
        return lines if lines else [text]

    def _load_font(
        self,
        font_path: str | None,
        size: int,
        fallback_paths: list[str] | None = None,
    ) -> ImageFont.FreeTypeFont:
        candidates = []
        if font_path:
            candidates.append(os.path.join(self.output_dir, font_path))
        if fallback_paths:
            candidates.extend(fallback_paths)

        for full_path in candidates:
            if os.path.exists(full_path):
                try:
                    return ImageFont.truetype(full_path, size)
                except Exception:
                    continue
        # Fall back to default PIL font
        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            return ImageFont.load_default()

    def _measure_tracking_text(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        font,
        tracking: int,
        stroke_width: int = 0,
    ) -> int:
        total = 0
        for i, ch in enumerate(text):
            bbox = draw.textbbox((0, 0), ch, font=font,
                                 stroke_width=stroke_width)
            char_w = bbox[2] - bbox[0]
            total += char_w
            if i < len(text) - 1:
                total += tracking
        return total

    def _draw_tracking_text_centered(
        self,
        draw: ImageDraw.ImageDraw,
        y: int,
        text: str,
        font,
        color: tuple[int, int, int],
        tracking: int = 2,
        stroke_width: int = 2,
    ) -> int:
        total_w = self._measure_tracking_text(
            draw, text, font, tracking, stroke_width)
        x = (self.TARGET_W - total_w) // 2
        max_h = 0
        for i, ch in enumerate(text):
            bbox = draw.textbbox((0, 0), ch, font=font,
                                 stroke_width=stroke_width)
            char_w = bbox[2] - bbox[0]
            char_h = bbox[3] - bbox[1]
            max_h = max(max_h, char_h)
            draw.text(
                (x, y),
                ch,
                font=font,
                fill=color,
                stroke_width=stroke_width,
                stroke_fill=color,
            )
            x += char_w + (tracking if i < len(text) - 1 else 0)
        return max_h

    def _wrap_line_tracking(
        self,
        text: str,
        font,
        draw: ImageDraw.ImageDraw,
        max_width: int,
        tracking: int = 2,
        stroke_width: int = 2,
    ) -> list[str]:
        words = text.split()
        lines = []
        current = ""
        for word in words:
            test = (current + " " + word).strip()
            width = self._measure_tracking_text(
                draw, test, font, tracking, stroke_width
            )
            if width <= max_width:
                current = test
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)
        return lines if lines else [text]


# ---------------------------------------------------------------------------
# Step 5: Upload image — Cloudinary (preferred) or ImgBB fallback
# ---------------------------------------------------------------------------

class ImageUploader:
    def __init__(self, config: dict):
        self.imgbb_key = config["image_hosting"].get("imgbb_api_key", "")

    def upload(self, image_path: str) -> str:
        """Try 0x0.st, then litterbox, then ImgBB."""
        for fn in (self._upload_0x0, self._upload_litterbox, self._upload_imgbb):
            try:
                return fn(image_path)
            except Exception as e:
                logging.warning(f"{fn.__name__} failed: {e}, trying next...")
        raise RuntimeError("All image upload services failed.")

    def _upload_0x0(self, image_path: str) -> str:
        with open(image_path, "rb") as f:
            resp = requests.post(
                "https://0x0.st",
                files={"file": ("image.jpg", f, "image/jpeg")},
                timeout=60,
            )
        resp.raise_for_status()
        url = resp.text.strip()
        if not url.startswith("http"):
            raise RuntimeError(f"Bad 0x0 response: {url}")
        logging.info(f"Image uploaded to 0x0.st: {url}")
        return url

    def _upload_litterbox(self, image_path: str) -> str:
        with open(image_path, "rb") as f:
            resp = requests.post(
                "https://litterbox.catbox.moe/resources/internals/api.php",
                data={"reqtype": "fileupload", "time": "72h"},
                files={"fileToUpload": ("image.jpg", f, "image/jpeg")},
                timeout=60,
            )
        resp.raise_for_status()
        url = resp.text.strip()
        if not url.startswith("http"):
            raise RuntimeError(f"Bad litterbox response: {url}")
        logging.info(f"Image uploaded to Litterbox: {url}")
        return url

    def _upload_imgbb(self, image_path: str) -> str:
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")

        resp = requests.post(
            "https://api.imgbb.com/1/upload",
            data={"key": self.imgbb_key, "image": b64},
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            raise RuntimeError(f"ImgBB upload failed: {data}")

        url = data["data"]["url"]
        logging.info(f"Image uploaded to ImgBB: {url}")
        return url


# ---------------------------------------------------------------------------
# Step 6: Publish to Instagram via Graph API
# ---------------------------------------------------------------------------

class InstagramPublisher:
    GRAPH_BASE = "https://graph.facebook.com/v18.0"

    def __init__(self, config: dict):
        self.access_token = config["instagram"]["access_token"]
        self.ig_user_id = config["instagram"]["ig_user_id"]
        self.hashtags = config["instagram"].get("hashtags", "")

    def publish(self, image_url: str, caption: str) -> dict:
        """Returns {'post_id': ..., 'post_url': ...}."""
        full_caption = f"{caption}\n\n{self.hashtags}".strip()

        # Step 1: Create media container
        container_resp = requests.post(
            f"{self.GRAPH_BASE}/{self.ig_user_id}/media",
            params={
                "image_url":    image_url,
                "caption":      full_caption,
                "access_token": self.access_token,
            },
            timeout=30,
        )
        if container_resp.status_code != 200:
            logging.error(f"Instagram API error: {container_resp.text}")
        container_resp.raise_for_status()
        container_id = container_resp.json().get("id")
        if not container_id:
            raise RuntimeError(
                f"Failed to create media container: {container_resp.text}")
        logging.info(f"Media container created: {container_id}")

        # Brief wait for Instagram to process the container
        time.sleep(5)

        # Step 2: Publish the container
        publish_resp = requests.post(
            f"{self.GRAPH_BASE}/{self.ig_user_id}/media_publish",
            params={
                "creation_id":  container_id,
                "access_token": self.access_token,
            },
            timeout=30,
        )
        publish_resp.raise_for_status()
        post_id = publish_resp.json().get("id", "")
        logging.info(f"Post published! ID: {post_id}")

        # Build post URL (best effort)
        post_url = f"https://www.instagram.com/p/{post_id}/" if post_id else ""
        return {"post_id": post_id, "post_url": post_url}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    config = load_config()
    setup_logging(config)
    logging.info("=== Post Generator starting ===")

    # Step 1: Get next ready topic
    sheets = SheetsReader(config)
    row = sheets.get_next_ready_row()
    if not row:
        logging.info(
            "No topics with Status='Ready' found. Nothing to post today.")
        print("NO_TOPIC: No ready topics in Google Sheet.")
        sys.exit(0)

    row_index = row["_row_index"]
    topic = row.get("Topic", "untitled")
    logging.info(f"Processing topic: {topic}")
    sheets.mark_in_progress(row_index)

    client = OpenAI(api_key=config["openai"]["api_key"])
    usage_guard = UsageGuard.from_env(config["output_dir"])
    usage_guard.start_run()

    # Carousel branch
    post_type = str(row.get("Post Type", "single")).strip().lower()
    if post_type == "carousel":
        from carousel_generator import CarouselBuilder
        CarouselBuilder(
            config, client, usage_guard=usage_guard).build_and_publish(row, sheets)
        sys.exit(0)

    # Step 2: Build caption — use pre-generated headlines from research sheet if available
    h1 = row.get("Headline Line 1 (White)", "").strip()
    h2 = row.get("Headline Line 2 (Neon Green)", "").strip()
    h3 = row.get("Headline Line 3 (White)", "").strip()
    sub = row.get("Subheadline (Gray)", "").strip()

    if h1 and h2 and h3:
        lines = [h1, h2, h3]
        if sub:
            lines.append(sub)
        caption = "\n".join(lines)
        logging.info("Using pre-generated headlines from research sheet")
    else:
        caption_gen = CaptionGenerator(config, client, usage_guard=usage_guard)
        gpt_output = caption_gen.generate(row)
        caption = gpt_output["post_caption"]

    # Step 3: Build DALL-E prompt + generate image
    key_points = row.get("Key Points / Slide Content", "") or row.get("Key Points", "")
    enriched_context = row.get("Enriched Context", "")
    img_prompt = build_dalle_prompt(row.get("Topic", ""), key_points, config,
                                    client=client, enriched_context=enriched_context)
    logging.info(f"DALL-E prompt: {img_prompt[:120]}...")
    img_gen = ImageGenerator(config, client, usage_guard=usage_guard)
    img_bytes = img_gen.generate(img_prompt)

    # Step 4: Pillow text overlay
    processor = ImageProcessor(config)
    # Prefer logo.png (white-bg version), fall back to example_image.png
    logo_path = os.path.join(config["output_dir"], "logo.png")
    if not os.path.exists(logo_path):
        logo_path = os.path.join(config["output_dir"], "example_image.png")
    img_path = processor.process(img_bytes, caption, logo_path=logo_path)

    # Step 5: Upload to ImgBB for public URL
    uploader = ImageUploader(config)
    public_url = uploader.upload(img_path)

    # Step 6: Publish to Instagram
    publisher = InstagramPublisher(config)
    result = publisher.publish(public_url, caption)

    # Step 7: Update Google Sheet
    sheets.mark_published(row_index, result["post_url"], result["post_id"])

    logging.info(f"Done! Post URL: {result['post_url']}")
    print(f"SUCCESS: Published '{topic}' — {result['post_url']}")
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except UsageLimitError as e:
        logging.error(f"Usage limit reached. Stopping run: {e}")
        print(f"PAUSED_BUDGET_LIMIT: {e}")
        sys.exit(2)
