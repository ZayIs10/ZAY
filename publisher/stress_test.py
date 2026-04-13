"""
Stress Test — runs the full pipeline up to (but NOT including) Instagram publish.
Steps covered:
  1. Google Sheets: read next "Ready" topic
  2. GPT-4o: generate caption
  3. DALL-E 3: generate image (via wealth-specific prompt)
  4. Pillow: two-zone composite + text overlay
  5. ImgBB: upload and get public URL
  5b. Google Sheets: write image URL to 'Image' column
  6. Instagram publish → SKIPPED

Usage:
  python stress_test.py              # single-image run
  python stress_test.py --runs 3     # run N times in a row
  python stress_test.py --dry-run    # skip API calls, use mock data
  python stress_test.py --carousel   # test carousel pipeline (8 slides, no publish)
"""

import argparse
import io
import json
import logging
import os
import sys
import time
from datetime import datetime

from post_generator import (
    CaptionGenerator,
    ImageGenerator,
    ImageProcessor,
    ImageUploader,
    SheetsReader,
    build_dalle_prompt,
    load_config,
    setup_logging,
)
from openai import OpenAI

SEPARATOR = "=" * 60


def safe_log(msg: str) -> str:
    """Encode message to ASCII for Windows terminals that lack UTF-8 support."""
    return msg.encode("ascii", "replace").decode("ascii")


def run_carousel(config: dict, dry_run: bool = False) -> dict:
    """Run the carousel pipeline (no Instagram publish). Returns result dict."""
    from carousel_generator import CarouselBuilder, CarouselContentGenerator

    results = {
        "start": datetime.utcnow().isoformat(),
        "steps": {},
        "passed": False,
        "error": None,
        "mode": "carousel",
    }

    try:
        t0 = time.time()
        sheets = None
        if dry_run:
            row = {
                "_row_index": 999,
                "Topic": "DRY RUN - How AI is making Gen Z rich",
                "Key Points": "AI trading bots, passive income automation, 34% portfolio growth",
                "Brand Tone": "Bold Gen Z",
                "Enriched Context": "Gen Z investors using AI tools saw 34% higher returns in 2025.",
                "Post Type": "carousel",
            }
            logging.info(safe_log("[DRY RUN] Using mock row for carousel test."))
        else:
            sheets = SheetsReader(config)
            row = sheets.get_next_ready_row()
            if not row:
                raise RuntimeError("No topics with Status='Ready' in Google Sheet.")
        results["steps"]["sheets_read"] = round(time.time() - t0, 2)
        logging.info(safe_log(
            f"Step 1 OK ({results['steps']['sheets_read']}s) — topic: {row.get('Topic')}"))

        client = OpenAI(api_key=config["openai"]["api_key"])

        t0 = time.time()
        builder = CarouselBuilder(config, client)
        result = builder.build_and_publish(row, sheets_reader=sheets, publish=False)
        results["steps"]["carousel_build"] = round(time.time() - t0, 2)
        results["slide_urls"] = result["slide_urls"]

        logging.info(safe_log(
            f"Carousel build OK ({results['steps']['carousel_build']}s) "
            f"— {len(result['slide_urls'])} slides"))
        for i, url in enumerate(result["slide_urls"], start=1):
            logging.info(safe_log(f"  Slide {i:02d}: {url}"))

        results["passed"] = True

    except Exception as exc:
        results["error"] = str(exc)
        logging.error(safe_log(f"CAROUSEL STRESS TEST FAILED: {exc}"), exc_info=True)

    results["end"] = datetime.utcnow().isoformat()
    results["duration"] = round(
        (datetime.fromisoformat(results["end"]) -
         datetime.fromisoformat(results["start"])).total_seconds(), 2)
    return results


def run_once(config: dict, dry_run: bool = False) -> dict:
    """Execute one full single-image pipeline run (no Instagram publish). Returns result dict."""
    results = {
        "start": datetime.utcnow().isoformat(),
        "steps": {},
        "passed": False,
        "error": None,
        "mode": "single",
    }

    try:
        # ── Step 1: Google Sheets ─────────────────────────────────────────────
        t0 = time.time()
        sheets = None
        if dry_run:
            row = {
                "_row_index": 999,
                "Topic": "DRY RUN - AI Automation",
                "Key Points": "Speed, accuracy, no sleep",
                "Brand Tone": "Bold Gen Z",
                "Enriched Context": "AI adoption grew 45% YoY in 2025.",
            }
            logging.info(safe_log("[DRY RUN] Using mock row instead of Google Sheets."))
        else:
            sheets = SheetsReader(config)
            row = sheets.get_next_ready_row()
            if not row:
                raise RuntimeError(
                    "No topics with Status='Ready' in Google Sheet.")
        results["steps"]["sheets_read"] = round(time.time() - t0, 2)
        logging.info(safe_log(
            f"Step 1 OK  ({results['steps']['sheets_read']}s) — topic: {row.get('Topic')}"))

        client = OpenAI(api_key=config["openai"]["api_key"])

        # ── Step 2: Caption ───────────────────────────────────────────────────
        t0 = time.time()
        if dry_run:
            gpt_output = {
                "post_caption": "THE MACHINES DON'T SLEEP\nWhy should you?",
            }
            logging.info(safe_log("[DRY RUN] Using mock caption."))
        else:
            caption_gen = CaptionGenerator(config, client)
            gpt_output = caption_gen.generate(row)
        results["steps"]["caption_gen"] = round(time.time() - t0, 2)
        caption = gpt_output["post_caption"]
        word_count = len(caption.strip().split())
        logging.info(safe_log(
            f"Step 2 OK  ({results['steps']['caption_gen']}s) — {word_count} words"))
        logging.info(safe_log(
            f"  Caption preview: {caption[:80].replace(chr(10), ' ')}"))

        # ── Step 3: DALL-E 3 image ────────────────────────────────────────────
        t0 = time.time()
        if dry_run:
            jpg_files = [
                f for f in os.listdir(config["output_dir"])
                if f.endswith(".jpg") and f.startswith("post_")
            ]
            if jpg_files:
                with open(os.path.join(config["output_dir"], jpg_files[-1]), "rb") as fh:
                    img_bytes = fh.read()
                logging.info(safe_log(f"[DRY RUN] Loaded existing image: {jpg_files[-1]}"))
            else:
                raise RuntimeError(
                    "--dry-run needs at least one post_*.jpg in the output dir.")
        else:
            img_prompt = build_dalle_prompt(
                row.get("Topic", ""), row.get("Key Points", ""), config)
            logging.info(safe_log(f"  DALL-E prompt: {img_prompt[:100]}..."))
            img_gen = ImageGenerator(config, client)
            img_bytes = img_gen.generate(img_prompt)
        results["steps"]["dalle_gen"] = round(time.time() - t0, 2)
        logging.info(safe_log(
            f"Step 3 OK  ({results['steps']['dalle_gen']}s) — {len(img_bytes):,} bytes"))

        # ── Step 4: Pillow overlay ────────────────────────────────────────────
        t0 = time.time()
        processor = ImageProcessor(config)
        logo_path = os.path.join(config["output_dir"], "logo.png")
        if not os.path.exists(logo_path):
            logo_path = os.path.join(config["output_dir"], "example_image.png")
        img_path = processor.process(img_bytes, caption, logo_path=logo_path)
        results["steps"]["pillow_overlay"] = round(time.time() - t0, 2)
        logging.info(safe_log(
            f"Step 4 OK  ({results['steps']['pillow_overlay']}s) — saved: {img_path}"))
        results["img_path"] = img_path

        # ── Step 5: ImgBB upload ──────────────────────────────────────────────
        t0 = time.time()
        if dry_run:
            public_url = "https://i.ibb.co/dry-run-placeholder/test.jpg"
            logging.info(safe_log("[DRY RUN] Skipping actual ImgBB upload."))
        else:
            uploader = ImageUploader(config)
            public_url = uploader.upload(img_path)
        results["steps"]["imgbb_upload"] = round(time.time() - t0, 2)
        logging.info(safe_log(
            f"Step 5 OK  ({results['steps']['imgbb_upload']}s) — url: {public_url}"))
        results["public_url"] = public_url

        # ── Step 5b: Write image URL back to Google Sheets "Image" column ────
        if sheets and not dry_run:
            try:
                sheets.write_image_url(row["_row_index"], public_url)
                logging.info(safe_log(
                    f"Step 5b OK — Image URL written to Sheets row {row['_row_index']}"))
            except Exception as e:
                logging.warning(safe_log(
                    f"Step 5b WARN — Could not write image URL to Sheets: {e}"))

        # ── Step 6: Instagram → SKIPPED ──────────────────────────────────────
        logging.info(safe_log(
            "Step 6 SKIPPED — Instagram publish intentionally disabled in stress test."))
        logging.info(safe_log(f"  Image URL ready for Instagram: {public_url}"))
        logging.info(safe_log(
            f"  Caption ready: {caption[:60].replace(chr(10), ' ')}"))

        results["passed"] = True

    except Exception as exc:
        results["error"] = str(exc)
        logging.error(safe_log(f"STRESS TEST FAILED: {exc}"), exc_info=True)

    results["end"] = datetime.utcnow().isoformat()
    results["duration"] = round(
        (datetime.fromisoformat(results["end"]) -
         datetime.fromisoformat(results["start"])).total_seconds(), 2)
    return results


def main():
    parser = argparse.ArgumentParser(
        description="Stress test post pipeline (no Instagram publish)")
    parser.add_argument("--runs",     type=int, default=1,
                        help="Number of sequential runs (default: 1)")
    parser.add_argument("--dry-run",  action="store_true",
                        help="Skip real API calls, use mock data")
    parser.add_argument("--carousel", action="store_true",
                        help="Test carousel pipeline instead of single image")
    args = parser.parse_args()

    config = load_config()
    setup_logging(config)

    logging.info(SEPARATOR)
    mode = "CAROUSEL" if args.carousel else "SINGLE IMAGE"
    logging.info(safe_log(
        f"STRESS TEST — {mode}  |  {args.runs} run(s)  |  dry-run={args.dry_run}"))
    logging.info(SEPARATOR)

    all_results = []
    for i in range(1, args.runs + 1):
        logging.info(safe_log(f"\n--- Run {i}/{args.runs} ---"))
        if args.carousel:
            result = run_carousel(config, dry_run=args.dry_run)
        else:
            result = run_once(config, dry_run=args.dry_run)
        all_results.append(result)
        status = "PASS" if result["passed"] else f"FAIL ({result['error']})"
        logging.info(safe_log(
            f"Run {i} result: {status}  |  total time: {result['duration']}s"))
        step_summary = "  |  ".join(
            f"{k}: {v}s" for k, v in result.get("steps", {}).items()
        )
        if step_summary:
            logging.info(safe_log(f"  Steps — {step_summary}"))

        if i < args.runs:
            logging.info(safe_log("Pausing 3s between runs..."))
            time.sleep(3)

    passed = sum(1 for r in all_results if r["passed"])
    failed = args.runs - passed
    logging.info(SEPARATOR)
    logging.info(safe_log(f"SUMMARY: {passed}/{args.runs} passed, {failed} failed"))
    if failed:
        for i, r in enumerate(all_results, 1):
            if not r["passed"]:
                logging.info(safe_log(f"  Run {i} error: {r['error']}"))
    logging.info(SEPARATOR)

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
