"""
batch_generate.py — Generate ALL "Ready" posts from Google Sheets in one run.

For each Ready topic:
  - Single Image: generates caption + DALL-E image + Pillow overlay → saved to assets/images/generated/
  - Carousel: generates 8 slides with content + images → saved to assets/images/generated/

Instagram publishing is SKIPPED (run post_generator.py individually when ready to publish).
Google Sheet status is updated to "Image Ready" after each post is generated.

Run: python publisher/batch_generate.py
"""

import json
import logging
import os
import shutil
import sys
from datetime import datetime

from dotenv import load_dotenv
from openai import OpenAI

# Make sure imports from publisher/ work
sys.path.insert(0, os.path.dirname(__file__))

from post_generator import (
    SheetsReader,
    CaptionGenerator,
    ImageGenerator,
    ImageProcessor,
    build_dalle_prompt,
    load_config,
    setup_logging,
)
from carousel_generator import CarouselBuilder
from usage_guard import UsageGuard, UsageLimitError

load_dotenv()


def get_output_dir(config: dict) -> str:
    out = os.path.join(config["output_dir"], "assets", "images", "generated")
    os.makedirs(out, exist_ok=True)
    return out


def mark_image_ready(sheets: SheetsReader, row_index: int) -> None:
    """Update row status to 'Image Ready' so it's easy to spot in the sheet."""
    try:
        status_col = sheets._col_index("Status")
        sheets.ws.update_cell(row_index, status_col, "Image Ready")
    except Exception as e:
        logging.warning(f"Could not update sheet status: {e}")


def generate_single_image(row: dict, config: dict, client: OpenAI,
                          usage_guard: UsageGuard, output_dir: str) -> list[str]:
    """Generate one Single Image post. Returns list of saved file paths."""
    topic = row.get("Topic", "untitled")
    logging.info(f"[Single] Generating post for: {topic}")

    # Caption
    h1 = row.get("Headline Line 1 (White)", "").strip()
    h2 = row.get("Headline Line 2 (Neon Green)", "").strip()
    h3 = row.get("Headline Line 3 (White)", "").strip()
    sub = row.get("Subheadline (Gray)", "").strip()

    if h1 and h2 and h3:
        lines = [h1, h2, h3]
        if sub:
            lines.append(sub)
        caption = "\n".join(lines)
        logging.info("[Single] Using pre-generated headlines from sheet")
    else:
        caption_gen = CaptionGenerator(config, client, usage_guard=usage_guard)
        gpt_output = caption_gen.generate(row)
        caption = gpt_output["post_caption"]

    logging.info(f"[Single] Caption: {caption[:80].encode('ascii','replace').decode()}...")

    # DALL-E image
    key_points = row.get("Key Points / Slide Content", "") or row.get("Key Points", "")
    img_prompt = build_dalle_prompt(topic, key_points, config)
    logging.info(f"[Single] DALL-E prompt: {img_prompt[:100]}...")
    img_gen = ImageGenerator(config, client, usage_guard=usage_guard)
    img_bytes = img_gen.generate(img_prompt)

    # Pillow overlay
    processor = ImageProcessor(config)
    logo_path = os.path.join(config["output_dir"], "logo.png")
    if not os.path.exists(logo_path):
        logo_path = os.path.join(config["output_dir"], "example_image.png")
    temp_path = processor.process(img_bytes, caption, logo_path=logo_path)

    # Move to assets/images/generated/ with topic-based name
    safe_topic = "".join(c if c.isalnum() or c in " _-" else "" for c in topic)[:40].strip().replace(" ", "_")
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    final_name = f"post_{ts}_{safe_topic}.jpg"
    final_path = os.path.join(output_dir, final_name)
    shutil.move(temp_path, final_path)
    logging.info(f"[Single] Saved: {final_path}")

    # Save caption alongside
    caption_path = os.path.join(output_dir, f"post_{ts}_{safe_topic}_caption.txt")
    with open(caption_path, "w", encoding="utf-8") as f:
        f.write(f"Topic: {topic}\n\n{caption}\n")
    logging.info(f"[Single] Caption saved: {caption_path}")

    return [final_path, caption_path]


def generate_carousel(row: dict, config: dict, client: OpenAI,
                      usage_guard: UsageGuard, output_dir: str) -> list[str]:
    """Generate carousel slides. Returns list of saved file paths."""
    topic = row.get("Topic", "untitled")
    logging.info(f"[Carousel] Generating carousel for: {topic}")

    builder = CarouselBuilder(config, client, usage_guard=usage_guard)
    result = builder.build_and_publish(row, sheets_reader=None, publish=False)

    # Slides are saved in the root output_dir by default — move them to assets/images/generated/
    safe_topic = "".join(c if c.isalnum() or c in " _-" else "" for c in topic)[:40].strip().replace(" ", "_")
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    moved_paths = []
    root_dir = config["output_dir"]
    # Find carousel slides just generated (they land in root with pattern carousel_*.jpg)
    existing = sorted([
        f for f in os.listdir(root_dir)
        if f.startswith("carousel_") and f.endswith(".jpg")
    ])
    # The latest batch (8 files) are the ones we just made — grab last 8
    latest_slides = existing[-8:] if len(existing) >= 8 else existing

    for i, fname in enumerate(latest_slides, start=1):
        src = os.path.join(root_dir, fname)
        dst = os.path.join(output_dir, f"carousel_{ts}_{safe_topic}_slide{i:02d}.jpg")
        shutil.move(src, dst)
        moved_paths.append(dst)
        logging.info(f"[Carousel] Slide {i} saved: {dst}")

    return moved_paths


def main():
    config = load_config()
    # Override output dir to match actual location
    config["output_dir"] = "C:/Users/Marc/Desktop/Gen Z autamation"
    setup_logging(config)

    logging.info("=" * 60)
    logging.info("=== Batch Post Generator starting ===")
    logging.info("=" * 60)

    sheets = SheetsReader(config)
    all_rows = sheets.ws.get_all_records()
    ready_rows = []
    for i, row in enumerate(all_rows, start=2):
        if str(row.get("Status", "")).strip().lower() == "ready":
            row["_row_index"] = i
            ready_rows.append(row)

    if not ready_rows:
        logging.info("No topics with Status='Ready' found. Nothing to generate.")
        print("NO_TOPICS: No ready topics found in Google Sheet.")
        sys.exit(0)

    logging.info(f"Found {len(ready_rows)} Ready topics:")
    for r in ready_rows:
        logging.info(f"  - [{r.get('Post Type','?')}] {r.get('Topic','')}")

    client = OpenAI(api_key=config["openai"]["api_key"])
    usage_guard = UsageGuard.from_env(config["output_dir"])
    usage_guard.start_run()

    output_dir = get_output_dir(config)
    generated = []
    failed = []

    for row in ready_rows:
        topic = row.get("Topic", "untitled")
        post_type = str(row.get("Post Type", "single")).strip().lower()
        row_index = row["_row_index"]

        print(f"\n{'='*50}")
        print(f"Generating: {topic}")
        print(f"Type: {post_type}")
        print(f"{'='*50}")

        try:
            if post_type == "carousel":
                paths = generate_carousel(row, config, client, usage_guard, output_dir)
            else:
                paths = generate_single_image(row, config, client, usage_guard, output_dir)

            mark_image_ready(sheets, row_index)
            generated.append((topic, post_type, paths))
            print(f"SUCCESS: {topic}")
            for p in paths:
                print(f"  -> {os.path.basename(p)}")

        except UsageLimitError as e:
            logging.error(f"Budget limit reached: {e}")
            print(f"\nPAUSED: Budget limit hit — stopping early.\n{e}")
            break
        except Exception as e:
            logging.error(f"Failed to generate post for '{topic}': {e}", exc_info=True)
            failed.append((topic, str(e)))
            print(f"FAILED: {topic} — {e}")
            # Revert row status to Ready so it can be retried
            try:
                sheets.ws.update_cell(row_index, sheets._col_index("Status"), "Ready")
            except Exception:
                pass
            continue

    # Summary
    print(f"\n{'='*60}")
    print(f"DONE — {len(generated)} posts generated, {len(failed)} failed")
    print(f"Output folder: {output_dir}")
    print(f"{'='*60}")
    for topic, ptype, paths in generated:
        print(f"\n[{ptype.upper()}] {topic}")
        for p in paths:
            print(f"  {os.path.basename(p)}")
    if failed:
        print(f"\nFAILED:")
        for topic, err in failed:
            print(f"  {topic}: {err}")


if __name__ == "__main__":
    main()
