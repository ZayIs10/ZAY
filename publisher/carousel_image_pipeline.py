"""
carousel_image_pipeline.py — Method A carousel image automation for Gen Z Capital.

This is the @evolving.ai "reference-first" image workflow turned into an
automation (see docs/evolving_ai_image_workflow.md). It is the CAROUSEL sibling
of the reels pipeline and reuses the same building blocks:

  reels:     row -> media_finder -> GitHub Actions -> render reel  -> Drive
  carousel:  row -> THIS script  -> GitHub Actions -> render deck  -> Drive

PER SLIDE, Method A (the part the user chose):
  1. FIND a real reference image for the slide's subject — reusing the reels'
     own media_finder.discover_for_topic() (yt-dlp / brand / Pexels / DDG).
  2. SEE it — GPT-4o vision looks at the reference and WRITES a single
     house-style text-to-image prompt (dark cinematic, neon-green accent,
     4:5, text-free, headline space at top).
  3. GENERATE — that prompt goes to the OpenAI image model -> cinematic bytes
     we own. (No reference found -> fall back to a pure text prompt from the
     slide's own words, so a slide is never blank.)
  4. SAVE the generated image where carousel_format.py expects it
     (assets/images/generated/<keyword>.png) so the renderer auto-picks it up.

Then build_carousel() renders the full deck and (optionally) we upload to Drive.

This file is API-driven (costs money): GPT-4o vision + image generation per
slide. It is guarded by usage_guard. Reference-FINDING is free (keyless).

CLI:
    # local dry run, no API calls — just find references + show the plan
    python publisher/carousel_image_pipeline.py --spec assets/carousels/foo.json --dry-run

    # full run: find refs, vision->prompt, generate images, render deck
    python publisher/carousel_image_pipeline.py --spec assets/carousels/foo.json

    # also upload the finished slides to Drive (cloud/review flow)
    python publisher/carousel_image_pipeline.py --spec ... --to-drive
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import re
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Reused building blocks ----------------------------------------------------
from publisher import carousel_format
from publisher.media_finder import discover_for_topic
from publisher.usage_guard import UsageGuard, UsageLimitError
from publisher.cover_director import choose_cover

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("carousel_image_pipeline")

GENERATED_DIR = REPO_ROOT / "assets" / "images" / "generated"

# The locked Gen Z Capital house style — every generated image obeys this so
# the whole feed looks like one brand (the style-consistency lesson from the
# evolving.ai research).
HOUSE_STYLE = (
    "ultra-realistic, cinematic, dark moody lighting, shallow depth of field, "
    "teal-and-black color grade with subtle neon-green (#39FF14) accents, high "
    "detail, shot on 35mm. Vertical 4:5 composition. CRITICAL FRAMING: place the "
    "main subject in the TOP 55-60% of the frame; the BOTTOM 40% must be clean, "
    "dark, near-black empty negative space (this is where the headline text is "
    "overlaid, so nothing important can be there). The subject must be fully "
    "visible above that bottom band, not cropped by it. "
    "Absolutely NO text, no words, no letters, no captions, no watermark, no "
    "logos, no UI chrome."
)

# What we ask GPT-4o vision to do with the reference image (Method A step 2).
VISION_INSTRUCTION = (
    "You are the art director for Gen Z Capital, a dark cinematic AI brand. "
    "Look at this reference image. Write ONE single text-to-image prompt that "
    "recreates its SUBJECT and COMPOSITION (what it shows and how it's framed) "
    "but rendered in our house style:\n" + HOUSE_STYLE + "\n"
    "Keep the real-world subject recognizable. IMPORTANT: if the reference "
    "contains any text, captions, slides, UI, charts, logos or watermarks, do "
    "NOT describe or reproduce them — describe only the PEOPLE, objects and "
    "setting. The generated image must contain zero readable text. Return "
    "ONLY the prompt text, no preamble, no quotes."
)


# ---------------------------------------------------------------------------
# OpenAI client (lazy — not needed for --dry-run)
# ---------------------------------------------------------------------------
def _client():
    from openai import OpenAI
    load_dotenv(REPO_ROOT / ".env")
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not set in .env")
    return OpenAI(api_key=key)


def _vision_model():
    return os.getenv("OPENAI_VISION_MODEL", "gpt-4o")


def _image_model():
    # gpt-image-1 supports reference + clean text; dall-e-3 is the safe fallback.
    return os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1")


# ---------------------------------------------------------------------------
# Step 1 — find a real reference image for a slide (FREE, reuses reels finder)
# ---------------------------------------------------------------------------
def _as_image_ref(cand: dict, *, via: str) -> dict:
    """Coerce any candidate into an image-reference dict (media_url = a real
    still picture). For a YouTube VIDEO winner, that still is its thumbnail
    frame — exactly the @evolving.ai move (a frame from the source company's
    own clip), not a generic stock photo."""
    thumb = cand.get("thumbnail") or cand.get("media_url")
    return {
        "media_url": thumb,
        "source": f"{cand.get('source')}_{via}",
        "title": cand.get("title"),
        "page_url": cand.get("page_url"),
    }


def find_reference(subject: str, key_points: str = "",
                   *, prefer_youtube: bool = True) -> dict | None:
    """Return the best reference-image candidate for `subject`, or None.

    Reuses the reels media_finder so we get the exact same brand/YouTube/Pexels
    ranking the reels already trust.

    Reference-image priority (the user wants the REAL YouTube frame, like
    @evolving.ai, not a stock photo):
      1. the VIDEO winner's YouTube thumbnail (prefer_youtube, default on) —
         a still from the actual source-company clip;
      2. otherwise the IMAGE winner (brand image / Pexels / DDG).
    Falls back gracefully whenever a tier is empty (e.g. YouTube blocked).
    """
    try:
        result = discover_for_topic(subject, key_points)
    except Exception as exc:
        log.warning("reference search failed for %r: %s", subject, exc)
        return None

    # 1) Prefer the YouTube video's thumbnail as the reference still.
    if prefer_youtube:
        vid = result.get("video", {}).get("winner")
        if vid and (vid.get("source") == "youtube"
                    or str(vid.get("source", "")).endswith("_official")):
            ref = _as_image_ref(vid, via="thumb")
            if ref.get("media_url"):
                log.info("  ref for %r -> %s (%s) [YouTube frame]", subject,
                         ref["media_url"][:60], ref["source"])
                return ref

    # 2) Fall back to the dedicated image winner.
    winner = result.get("image", {}).get("winner")
    if winner and winner.get("media_url"):
        log.info("  ref for %r -> %s (%s)", subject,
                 winner.get("media_url", "")[:60], winner.get("source"))
        return winner

    log.info("  no reference image found for %r", subject)
    return None


def _download(url: str) -> bytes | None:
    try:
        r = requests.get(url, timeout=45)
        r.raise_for_status()
        return r.content
    except Exception as exc:
        log.warning("  could not download reference %s: %s", url[:60], exc)
        return None


# ---------------------------------------------------------------------------
# Step 2 — GPT-4o vision: reference image -> house-style prompt
# ---------------------------------------------------------------------------
def vision_to_prompt(client, image_bytes: bytes, subject: str,
                     guard: UsageGuard | None = None) -> str:
    """Ask GPT-4o to look at the reference and write our house-style prompt."""
    b64 = base64.b64encode(image_bytes).decode()
    resp = client.chat.completions.create(
        model=_vision_model(),
        temperature=0.7,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": VISION_INSTRUCTION +
                 f"\n\n(The slide is about: {subject})"},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ],
        }],
    )
    if guard is not None:
        guard.register_chat_usage(getattr(resp, "usage", None))
    prompt = resp.choices[0].message.content.strip().strip('"')
    log.info("  vision prompt: %s", prompt[:90].replace("\n", " ") + "...")
    return prompt


def text_only_prompt(subject: str) -> str:
    """Fallback when no reference image exists — build a prompt from the slide
    subject alone so the slide still gets a real cinematic image."""
    return f"A cinematic scene representing: {subject}. {HOUSE_STYLE}"


# ---------------------------------------------------------------------------
# Step 3 — generate the cinematic image from the prompt
# ---------------------------------------------------------------------------
def generate_image(client, prompt: str,
                   guard: UsageGuard | None = None) -> bytes:
    """Generate a 1024x1536 (≈4:5) image and return PNG bytes."""
    model = _image_model()
    # Charge the guard BEFORE the call so the cap is enforced, not just logged.
    # gpt-image-1 1024x1536 and dall-e-3 hd both bill at the HD tier here.
    if guard is not None:
        guard.register_image_generation("hd")
    try:
        if model.startswith("gpt-image"):
            resp = client.images.generate(
                model=model, prompt=prompt, size="1024x1536", n=1)
            b64 = resp.data[0].b64_json
            if b64:
                return base64.b64decode(b64)
            return _download(resp.data[0].url)
        # dall-e-3 path
        resp = client.images.generate(
            model="dall-e-3", prompt=prompt, size="1024x1792",
            quality="hd", style="vivid", n=1, response_format="url")
        return _download(resp.data[0].url)
    except Exception as exc:
        log.error("  image generation failed: %s", exc)
        raise


# ---------------------------------------------------------------------------
# Orchestrator — walk the carousel spec, fill every image slide
# ---------------------------------------------------------------------------
def _slide_keyword(beat: dict, idx: int) -> str:
    """The filename keyword carousel_format uses to locate this slide's bg.
    Prefer the first media_keyword in the spec so the renderer matches it."""
    kws = beat.get("media_keywords") or []
    return kws[0] if kws else f"carousel_slide_{idx+1}"


# --- @evolving.ai keyword rule ---------------------------------------------
# The user's locked format: from the TOPIC, pull the REAL keywords (the actual
# AI company / product / the PERSON behind it) and search THOSE on YouTube /
# Pexels — not generic mood words. Prefer the person behind it + the brand.
#
# Known AI brands -> the public person most associated with them, so a topic
# like "Anthropic ... Claude" auto-searches the real founder's face + the brand
# (the @evolving.ai real-face-plus-logo look). Add new brands as the niche grows.
BRAND_PEOPLE = {
    "anthropic": "Dario Amodei",
    "claude": "Dario Amodei",
    "openai": "Sam Altman",
    "chatgpt": "Sam Altman",
    "gpt": "Sam Altman",
    "google": "Sundar Pichai",
    "gemini": "Sundar Pichai",
    "deepmind": "Demis Hassabis",
    "meta": "Mark Zuckerberg",
    "llama": "Mark Zuckerberg",
    "xai": "Elon Musk",
    "grok": "Elon Musk",
    "tesla": "Elon Musk",
    "nvidia": "Jensen Huang",
    "microsoft": "Satya Nadella",
    "copilot": "Satya Nadella",
    "mistral": "Arthur Mensch",
    "perplexity": "Aravind Srinivas",
    "midjourney": "David Holz",
}

# Common English words that look like proper nouns at sentence start — never a
# brand. Keeps the extractor from treating "The", "New", "AI" as keywords.
_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "to", "for", "of", "and", "or", "in",
    "on", "says", "say", "said", "new", "now", "this", "that", "too", "ai",
    "it", "its", "public", "use", "risky", "report", "reportedly", "model",
}


def topic_keywords(topic: str, headline: str = "") -> dict:
    """Extract the REAL search keywords from a topic the @evolving.ai way.

    Returns {"brands": [...], "person": str|None, "query": str} where:
      - brands  = real AI companies/products named (Claude, Anthropic, ...)
      - person  = the public person behind the first known brand (CEO/creator)
      - query   = the search phrase to hand to media_finder, PERSON + BRAND first
                  (e.g. "Dario Amodei Anthropic Claude") so YouTube/Pexels return
                  the real face + brand, not stock mood footage.
    """
    text = f"{topic} {headline}"
    low = text.lower()

    # 1) brands = known brand words that appear in the topic, in order.
    brands, seen = [], set()
    for word in re.findall(r"[A-Za-z][A-Za-z0-9.\-]*", text):
        w = word.lower().strip(".-")
        if w in BRAND_PEOPLE and w not in seen:
            brands.append(word)
            seen.add(w)

    # 2) person = the public face of the first recognized brand.
    person = None
    for w in (b.lower().strip(".-") for b in brands):
        if BRAND_PEOPLE.get(w):
            person = BRAND_PEOPLE[w]
            break

    # 3) any other capitalized proper nouns (a person's name in the headline,
    #    a product) so we don't miss subjects not in the brand map.
    extras = []
    for word in re.findall(r"\b[A-Z][a-zA-Z]+\b", text):
        if word.lower() not in _STOPWORDS and word.lower() not in seen \
                and word not in extras:
            extras.append(word)

    # 4) build the query: PERSON first (the face), then brand, then one extra.
    parts = []
    if person:
        parts.append(person)
    parts.extend(brands[:2])
    parts.extend([e for e in extras if e not in " ".join(parts)][:1])
    query = " ".join(dict.fromkeys(parts)).strip() or topic.strip()
    return {"brands": brands, "person": person, "query": query}


def _subject_for(beat: dict, spec: dict | None = None) -> str:
    """The clean VISUAL search phrase for this slide's reference image.

    Priority:
      1. beat["ref_query"]  — an explicit, hand-tuned visual phrase (best)
      2. COVER ONLY: auto keywords from the TOPIC (person + brand) — the locked
         @evolving.ai rule, so the hero is the real face/brand without hand-
         writing a query.
      3. beat["media_keywords"] joined — the renderer keywords double as a query
      4. the headline alone — never the body (bodies make terrible image queries)
    Headlines/kickers are marketing copy, not search terms, so we keep this short
    and concrete; a noisy query is why some slides found no reference.
    """
    if beat.get("ref_query"):
        return beat["ref_query"].strip()
    # The cover is the hero slide — drive its search from the topic keywords
    # (person + brand) so it matches the @evolving.ai real-face-plus-logo format.
    if beat.get("type") == "cover" and spec and spec.get("topic"):
        kw = topic_keywords(spec["topic"], beat.get("headline", ""))
        if kw["query"]:
            return kw["query"]
    # else: the 2nd media_keyword is the visual one (the 1st is the slide-id
    # filename like 'claude_write_1'); fall back to the headline.
    kws = beat.get("media_keywords") or []
    visual_kws = [k for k in kws if not any(ch.isdigit() for ch in k)]
    if visual_kws:
        return " ".join(visual_kws[:2])
    return beat.get("headline", "").strip()


def run(spec: dict, *, dry_run: bool = False, to_drive: bool = False,
        max_slides: int | None = None, update_sheet: bool = False) -> dict:
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    client = None if dry_run else _client()
    plan = []

    # Budget guard — only on real (paid) runs. Reads OPENAI_*_BUDGET_USD from
    # .env (same caps as post_generator). start_run() resets the per-run
    # counter and refuses to start if a daily/monthly cap is already blown.
    guard: UsageGuard | None = None
    if not dry_run:
        guard = UsageGuard.from_env(str(REPO_ROOT))
        guard.start_run()

    image_beats = [
        (i, b) for i, b in enumerate(spec["slides"])
        if b.get("type") in ("cover", "content")  # recap/cta/video skip gen
    ]
    if max_slides is not None:
        image_beats = image_beats[:max_slides]
        log.info("Limiting to first %d image slide(s) for this run.",
                 max_slides)
    log.info("Method A image gen for %d image slides (dry_run=%s)",
             len(image_beats), dry_run)

    for idx, beat in image_beats:
        # COVER ART DIRECTION: pick a flexible archetype (CEO face / staged
        # scene / symbolic object / product / before-after / money) per topic
        # so covers stop the scroll AND match the story — not one rigid mold.
        # A hand-written beat["prompt"] still wins; this only fills the gap.
        if beat.get("type") == "cover" and not beat.get("prompt"):
            kw = topic_keywords(spec.get("topic", ""), beat.get("headline", ""))
            director = choose_cover(
                spec.get("topic", ""), beat.get("headline", ""),
                person=kw.get("person"), fmt=spec.get("format"))
            beat["prompt"] = director["prompt"]
            log.info("Cover archetype: %s (accent=%s)",
                     director["archetype"], director["accent"])

        subject = _subject_for(beat, spec)
        keyword = _slide_keyword(beat, idx)
        out_path = GENERATED_DIR / f"{keyword}.png"
        entry = {"slide": idx + 1, "subject": subject, "keyword": keyword}

        ref = find_reference(subject, beat.get("body", ""))
        entry["reference_url"] = (ref or {}).get("media_url")
        entry["reference_source"] = (ref or {}).get("source")

        if dry_run:
            entry["action"] = ("vision->prompt->generate" if ref
                               else "text-only->generate")
            plan.append(entry)
            continue

        # Step 2 + 3
        # An explicit beat["prompt"] OVERRIDES the vision step — use the
        # hand-approved prompt verbatim (also skips the vision API cost). This
        # is how we lock an exact subject like the porcelain-android hero
        # instead of whatever happened to be in the reference frame.
        override = beat.get("prompt")
        ref_bytes = None if override else (_download(ref["media_url"]) if ref else None)
        try:
            if override:
                prompt = f"{override.strip()} {HOUSE_STYLE}"
                entry["reference_source"] = "spec_prompt_override"
            elif ref_bytes:
                prompt = vision_to_prompt(client, ref_bytes, subject, guard)
            else:
                prompt = text_only_prompt(subject)
            entry["prompt"] = prompt

            img_bytes = generate_image(client, prompt, guard)
        except UsageLimitError as exc:
            log.error("Stopped by usage guard: %s", exc)
            entry["error"] = f"usage-limit: {exc}"
            plan.append(entry)
            break
        out_path.write_bytes(img_bytes)
        entry["saved"] = str(out_path.relative_to(REPO_ROOT))
        log.info("  [slide %d] saved -> %s", idx + 1, entry["saved"])
        plan.append(entry)

    if dry_run:
        log.info("DRY RUN — no API calls, no images generated.")
        print(json.dumps({"plan": plan}, indent=2))
        return {"plan": plan, "rendered": None}

    # Step 4 — render the full deck (renderer auto-finds the generated images)
    paths = carousel_format.build_carousel(spec)

    cost_line = ""
    if guard is not None:
        snap = guard.snapshot()
        cost_line = (f"${snap.run_usd:.4f} ({snap.run_requests} requests); "
                     f"day=${snap.daily_usd:.2f} month=${snap.monthly_usd:.2f}")
        log.info("Run cost: %s", cost_line)

    # Step 5 — the REVIEW GATE (user's rule: nothing reaches Instagram
    # unsupervised). Upload the deck FOLDER to Drive, email the link +
    # caption for review, and mark the Sheet row Ready to Post.
    review = {}
    if to_drive and paths:
        from publisher.carousel_review import review_gate  # noqa: E402
        deck_dir = Path(paths[0]).parent
        caption_file = deck_dir / "caption.txt"
        caption = (caption_file.read_text(encoding="utf-8")
                   if caption_file.exists() else "")
        review = review_gate(
            topic=spec.get("topic", ""), deck_dir=deck_dir, caption=caption,
            n_slides=len(paths), cost_line=cost_line,
            update_sheet=update_sheet)

    return {"plan": plan, "rendered": paths, "review": review}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--spec", required=True, help="carousel spec JSON path")
    ap.add_argument("--dry-run", action="store_true",
                    help="find references + print the plan, NO API calls")
    ap.add_argument("--to-drive", action="store_true",
                    help="upload finished slides to Google Drive")
    ap.add_argument("--max-slides", type=int, default=None,
                    help="only generate the first N image slides "
                         "(e.g. 1 = single-slide test run)")
    ap.add_argument("--update-sheet", action="store_true",
                    help="write the Sheet row status (Ready to Post / "
                         "Render Failed) — used by the cloud workflow")
    args = ap.parse_args()

    spec_path = Path(args.spec)
    if not spec_path.is_absolute():
        spec_path = REPO_ROOT / spec_path
    with open(spec_path, encoding="utf-8") as fh:
        spec = json.load(fh)

    try:
        run(spec, dry_run=args.dry_run, to_drive=args.to_drive,
            max_slides=args.max_slides, update_sheet=args.update_sheet)
    except Exception:
        # A failed build must release the Sheet row from "Building" so the
        # user sees it and can reset to "Ready to Run" (reels semantics).
        if args.update_sheet and not args.dry_run:
            from publisher.carousel_review import (update_sheet_status,
                                                   FAILED_STATUS)
            update_sheet_status(spec.get("topic", ""), FAILED_STATUS)
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
