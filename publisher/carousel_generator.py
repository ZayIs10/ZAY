"""
carousel_generator.py — Carousel post pipeline for Gen Z Capital.

Generates an 8-slide Instagram carousel:
  Slide 1: Cover (hook headline, neon accent)
  Slides 2-6: Content (one key insight each)
  Slide 7: Stat (big neon number + label)
  Slide 8: CTA (Save This / Follow For More)

Usage:
  Called from post_generator.py when Post Type = 'carousel'
  Or from stress_test.py --carousel for local preview testing.
"""

import json
import logging
import os
import re
import time

import requests
from openai import OpenAI

from post_generator import (ImageGenerator, ImageProcessor,
                             ImageUploader, build_dalle_prompt)
from usage_guard import UsageGuard


def _strip_urls(text: str) -> str:
    """Remove all HTTP/HTTPS URLs from text so they never appear in captions."""
    return re.sub(r'https?://\S+', '', text).strip()


# ---------------------------------------------------------------------------
# Slide content generation via GPT-4o
# ---------------------------------------------------------------------------

SLIDE_SYSTEM_PROMPT = (
    "You are a carousel content architect for Gen Z Capital, a wealth/AI/automation brand. "
    "Tone: bold, direct, no fluff, no emojis. Dark cinematic aesthetic. "
    "Each slide must have: "
    "slide_type (string), headline (string, max 7 words), "
    "body (string, max 18 words, empty string for cover/stat/cta), "
    "accent_number (string, only for stat slide, else empty string).\n\n"
    "Slide structure:\n"
    "  [0] slide_type='cover'   — Hook headline. Must start with a bold number or shocking claim. "
    "Must end with unresolved tension that forces a swipe. Max 6 words. "
    "Example: '97% of people miss this.'\n"
    "  [1] slide_type='content' — Key insight 1. Short headline + 1-sentence body.\n"
    "  [2] slide_type='content' — Key insight 2.\n"
    "  [3] slide_type='content' — Key insight 3.\n"
    "  [4] slide_type='content' — Key insight 4.\n"
    "  [5] slide_type='content' — Key insight 5.\n"
    "  [6] slide_type='stat'    — One powerful statistic. accent_number = the number (e.g. '47%'), "
    "headline = short label (e.g. 'of Gen Z uses AI daily').\n"
    "  [7] slide_type='cta'     — headline = 'SAVE THIS.' always. No other options.\n\n"
    'Return ONLY valid JSON in this exact format: {"slides": [<8 slide objects>]}'
)


class CarouselContentGenerator:
    def __init__(self, config: dict, client: OpenAI, usage_guard: UsageGuard | None = None):
        self.client = client
        self.model = config["openai"]["model"]
        self.temperature = config["openai"].get("temperature", 0.85)
        self.usage_guard = usage_guard

    def generate_slides(self, row: dict) -> list:
        """Returns list of 8 slide dicts."""
        topic = row.get("Topic", "")
        key_points = row.get("Key Points / Slide Content", "") or row.get("Key Points", "")
        enriched = _strip_urls(row.get("Enriched Context", ""))

        context_block = f"\nReal-world context: {enriched}" if enriched else ""

        user_msg = (
            f"Topic: {topic}\n"
            f"Key Points: {key_points}\n"
            f"{context_block}\n\n"
            "Generate the 8-slide carousel content."
        )

        for attempt in range(2):
            resp = self.client.chat.completions.create(
                model=self.model,
                temperature=self.temperature,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": SLIDE_SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
            )
            if self.usage_guard:
                self.usage_guard.register_chat_usage(
                    getattr(resp, "usage", None))
            raw = resp.choices[0].message.content.strip()

            try:
                parsed = json.loads(raw)
                slides = parsed.get("slides", parsed) if isinstance(parsed, dict) else parsed
                if isinstance(slides, list) and len(slides) == 8:
                    logging.info(f"Carousel content generated: {len(slides)} slides")
                    return slides
                logging.warning(
                    f"GPT returned {len(slides) if isinstance(slides, list) else 'non-list'} "
                    f"slides (attempt {attempt+1}), retrying...")
            except json.JSONDecodeError as e:
                logging.warning(f"Slide JSON parse error (attempt {attempt+1}): {e}")

        raise RuntimeError(
            "Failed to generate exactly 8 carousel slides after 2 attempts.")


# ---------------------------------------------------------------------------
# Carousel builder — orchestrates all 8 slides
# ---------------------------------------------------------------------------

GRAPH_BASE = "https://graph.facebook.com/v18.0"


class CarouselBuilder:
    def __init__(self, config: dict, client: OpenAI, usage_guard: UsageGuard | None = None):
        self.config = config
        self.client = client
        self.ig_cfg = config["instagram"]
        self.usage_guard = usage_guard

    def build_and_publish(self, row: dict, sheets_reader=None, publish: bool = True) -> dict:
        """Full carousel pipeline. Returns dict with image_urls, post_id, post_url.

        Args:
            row: Google Sheets row dict with Topic, Key Points, etc.
            sheets_reader: SheetsReader instance (for writing image URLs back to sheet).
            publish: If False, skips Instagram API calls (for stress testing).
        """
        topic = row.get("Topic", "untitled")
        key_points = row.get("Key Points / Slide Content", "") or row.get("Key Points", "")
        row_index = row.get("_row_index")

        logging.info(f"[Carousel] Building carousel for: {topic}")

        # Step 1: Generate slide content
        content_gen = CarouselContentGenerator(
            self.config, self.client, usage_guard=self.usage_guard)
        slides = content_gen.generate_slides(row)

        # Step 2: Build DALL-E prompt base using AI (topic-specific cinematic scene)
        enriched_context = row.get("Enriched Context", "")
        base_subject_prompt = build_dalle_prompt(
            topic, key_points, self.config,
            client=self.client, enriched_context=enriched_context)

        # Step 3: Generate image + overlay for each slide
        img_gen = ImageGenerator(
            self.config, self.client, usage_guard=self.usage_guard)
        processor = ImageProcessor(self.config)
        logo_path = os.path.join(self.config["output_dir"], "logo.png")
        if not os.path.exists(logo_path):
            logo_path = os.path.join(
                self.config["output_dir"], "example_image.png")

        slide_paths = []
        for i, slide in enumerate(slides, start=1):
            slide_type = slide.get("slide_type", "content")

            # Cover and stat use HD; content slides use standard to save cost
            quality = "hd" if slide_type in ("cover", "stat") else "standard"

            logging.info(
                f"[Carousel] Generating slide {i}/8 ({slide_type})...")
            img_bytes = img_gen.generate(base_subject_prompt, quality=quality)
            slide_path = processor.process_carousel_slide(
                slide, img_bytes, i, len(slides), logo_path=logo_path)
            slide_paths.append(slide_path)

        # Step 4: Upload all slides to ImgBB
        uploader = ImageUploader(self.config)
        slide_urls = []
        for i, path in enumerate(slide_paths, start=1):
            logging.info(f"[Carousel] Uploading slide {i}/8 to ImgBB...")
            url = uploader.upload(path)
            slide_urls.append(url)

        logging.info(f"[Carousel] All {len(slide_urls)} slides uploaded.")

        # Write image URLs to Google Sheets Image column (comma-separated)
        if sheets_reader and row_index:
            try:
                sheets_reader.write_image_url(row_index, ", ".join(slide_urls))
                logging.info(
                    f"[Carousel] Image URLs written to Sheets row {row_index}")
            except Exception as e:
                logging.warning(
                    f"[Carousel] Could not write image URLs to Sheets: {e}")

        if not publish:
            logging.info("[Carousel] Publish skipped (stress test mode).")
            logging.info(f"[Carousel] Slide URLs:\n" + "\n".join(
                f"  Slide {i+1}: {u}" for i, u in enumerate(slide_urls)))
            return {"slide_urls": slide_urls, "post_id": None, "post_url": None}

        # Step 5: Publish to Instagram as carousel
        caption = self._build_caption(row, slides)
        post_id, post_url = self._publish_carousel(slide_urls, caption)

        logging.info(f"[Carousel] Published! Post ID: {post_id}")
        return {"slide_urls": slide_urls, "post_id": post_id, "post_url": post_url}

    def _build_caption(self, row: dict, slides: list) -> str:
        """Build the full Instagram caption from the row data + CTA slide."""
        topic = row.get("Topic", "")
        hashtags = self.ig_cfg.get("hashtags", "")
        cta = "Save this. Send it to someone building wealth."
        return f"{topic}\n\n{cta}\n\n{hashtags}".strip()

    def _publish_carousel(self, slide_urls: list, caption: str) -> tuple:
        """Create child containers → carousel container → publish. Returns (post_id, post_url)."""
        access_token = self.ig_cfg["access_token"]
        ig_user_id = self.ig_cfg["ig_user_id"]

        # Create child media containers
        child_ids = []
        for i, url in enumerate(slide_urls, start=1):
            resp = requests.post(
                f"{GRAPH_BASE}/{ig_user_id}/media",
                params={
                    "image_url": url,
                    "is_carousel_item": "true",
                    "access_token": access_token,
                },
                timeout=30,
            )
            if resp.status_code != 200:
                logging.error(
                    f"[Carousel] Child container {i} error: {resp.text}")
            resp.raise_for_status()
            child_id = resp.json().get("id")
            if not child_id:
                raise RuntimeError(
                    f"No container ID returned for slide {i}: {resp.text}")
            child_ids.append(child_id)
            logging.info(f"[Carousel] Child container {i} created: {child_id}")
            time.sleep(2)  # avoid rate limiting

        # Create carousel container
        carousel_resp = requests.post(
            f"{GRAPH_BASE}/{ig_user_id}/media",
            params={
                "media_type": "CAROUSEL",
                "caption": caption,
                "children": ",".join(child_ids),
                "access_token": access_token,
            },
            timeout=30,
        )
        if carousel_resp.status_code != 200:
            logging.error(
                f"[Carousel] Carousel container error: {carousel_resp.text}")
        carousel_resp.raise_for_status()
        carousel_id = carousel_resp.json().get("id")
        if not carousel_id:
            raise RuntimeError(
                f"No carousel container ID: {carousel_resp.text}")
        logging.info(f"[Carousel] Carousel container created: {carousel_id}")

        time.sleep(5)  # let Instagram process the container

        # Publish
        pub_resp = requests.post(
            f"{GRAPH_BASE}/{ig_user_id}/media_publish",
            params={
                "creation_id": carousel_id,
                "access_token": access_token,
            },
            timeout=30,
        )
        pub_resp.raise_for_status()
        post_id = pub_resp.json().get("id", "")
        post_url = f"https://www.instagram.com/p/{post_id}/" if post_id else ""
        return post_id, post_url
