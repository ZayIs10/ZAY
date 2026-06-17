"""
carousel_templates.py — the 3 locked carousel FORMATS for Gen Z Capital.

The deep-research conclusion (2026-06-12): of @evolving.ai's carousel
categories, the ones worth copying for this brand are, in order:

  1. tutorial     — "How to do X with AI", step slides. Best fit: the niche
                    rule says every post must teach a usable skill; tutorials
                    are the only format that IS the skill. Drives saves.
  2. listicle     — "N AI tools that ...". Top engagement type, trivially
                    automatable, one micro-skill per slide keeps it on-niche.
  3. news_hybrid  — news hook -> what it means -> the usable takeaway. Use
                    only when a story is big; never pure news with no skill.

This module bakes that into the automation:

  - FORMATS:               the slide-structure template per format
  - choose_format():       FREE keyword heuristic topic -> format (no GPT)
  - skeleton_spec():       FREE — spec with the right slide mix + TODO copy
  - draft_spec():          PAID (cheap chat call, usage-guarded) — GPT fills
                           the skeleton with real copy. NEVER invents facts:
                           only the topic/key_points/source text provided.

The output is a normal carousel spec (the JSON carousel_format.build_carousel
and carousel_image_pipeline.run already consume), with one new top-level key:
    "format": "tutorial" | "listicle" | "news_hybrid"

CLI:
    # free: classify + write a skeleton spec to fill in by hand
    python publisher/carousel_templates.py --topic "How to ..." --skeleton

    # paid (one cheap chat call): GPT drafts the full spec
    python publisher/carousel_templates.py --topic "..." --key-points "..."
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from publisher.carousel_format import slugify  # noqa: E402
from publisher.usage_guard import UsageGuard  # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("carousel_templates")

SPECS_DIR = REPO_ROOT / "assets" / "carousels"


# ---------------------------------------------------------------------------
# The 3 formats — slide plans + per-format writing rules
# ---------------------------------------------------------------------------
# Each slide-plan entry maps 1:1 to a slide the renderer understands
# (cover / content / recap / cta). "kicker" here is GUIDANCE the drafter
# follows; "n" expands STEP/ITEM slides to however many the content needs
# (within min..max).
FORMATS: dict[str, dict] = {
    "tutorial": {
        "label": "Tutorial / How-to",
        "goal": ("Teach ONE usable AI skill end-to-end. The reader must be "
                 "able to DO the thing after swiping — exact tool, exact "
                 "clicks, exact prompt text where relevant."),
        "slide_plan": [
            {"type": "cover", "kicker": "AI HOW-TO",
             "note": "Headline = the RESULT the reader gets, not the topic. "
                     "counter chip = effort/cost (e.g. FREE / 2 MIN)."},
            {"type": "content", "kicker": "STEP {n}", "n": (3, 5),
             "note": "One concrete action per slide. Body says exactly what "
                     "to open/click/type. If a prompt is part of the step, "
                     "include the literal prompt text."},
            {"type": "content", "kicker": "PRO TIP",
             "note": "One non-obvious upgrade to the basic steps."},
            {"type": "recap", "note": "The steps, one line each."},
            {"type": "cta"},
        ],
        "rules": ("Steps must be concrete and reproducible. No vague advice "
                  "('experiment with settings'). Name the actual tool/menu/"
                  "button. Free options first."),
    },
    "listicle": {
        "label": "Tool listicle",
        "goal": ("N AI tools/ways for one job. Each item slide teaches a "
                 "MICRO-SKILL: not just what the tool is, but one concrete "
                 "way to use it today."),
        "slide_plan": [
            {"type": "cover", "kicker": "AI TOOLS",
             "note": "Headline contains the count ('5 AI Tools That ...'). "
                     "counter chip = that number."},
            {"type": "content", "kicker": "TOOL {n}", "n": (4, 6),
             "note": "Headline = the tool name + what it wins you. Body = "
                     "what it does in one line, then one concrete use "
                     "('paste your X, ask it to Y'). Mention if free."},
            {"type": "recap", "note": "The list again, one line per tool."},
            {"type": "cta"},
        ],
        "rules": ("Only name tools that actually exist and do what is "
                  "claimed. If unsure a tool does something, leave it out. "
                  "Free tools first, paid clearly marked."),
    },
    "news_hybrid": {
        "label": "News -> skill hybrid",
        "goal": ("A big AI story as the hook, but the post must still END in "
                 "something the reader can USE. News alone is off-brand."),
        "slide_plan": [
            {"type": "cover", "kicker": "AI NEWS",
             "note": "Headline = the story. counter chip = its key number/"
                     "phrase. No ref_query on the cover — topic_keywords() "
                     "auto-finds the real person + brand."},
            {"type": "content", "kicker": "THE REPORT",
             "note": "ONLY the facts provided. Unconfirmed details get "
                     "'reportedly' / 'according to the report' framing."},
            {"type": "content", "kicker": "THE PATTERN",
             "note": "The wider shift this story is one example of — true "
                     "regardless of the unconfirmed specifics."},
            {"type": "content", "kicker": "WHAT IT MEANS FOR YOU",
             "note": "Why a normal person should care."},
            {"type": "content", "kicker": "THE SKILL",
             "note": "REQUIRED — the usable takeaway/action. This slide is "
                     "what keeps news on-niche."},
            {"type": "recap"},
            {"type": "cta"},
        ],
        "rules": ("HARD RULE: never invent facts, numbers, quotes, dates or "
                  "names. If a specific is not in the provided topic/key "
                  "points/source, either attribute it ('reportedly') or "
                  "leave it out. The recap must note when details are "
                  "unconfirmed."),
    },
}

VALID_FORMATS = tuple(FORMATS)


# ---------------------------------------------------------------------------
# FREE: topic -> format (keyword heuristic, no GPT)
# ---------------------------------------------------------------------------
_TUTORIAL_PAT = re.compile(
    r"\bhow to\b|\bstep[- ]by[- ]step\b|\btutorial\b|\bguide\b|\bturn .+ into\b"
    r"|\buse .+ to\b|\bbuild\b|\bmake\b|\bcreate .+ with\b|\bautomate\b",
    re.IGNORECASE)
_LISTICLE_PAT = re.compile(
    r"^\s*\d+\s|\b\d+\s+(?:ai\s+)?(?:tools?|apps?|ways?|tips?|sites?|prompts?"
    r"|hacks?|extensions?)\b|\bbest (?:ai )?(?:tools?|apps?)\b",
    re.IGNORECASE)
_NEWS_PAT = re.compile(
    r"\b(?:says?|said|announc\w+|launch\w+|releas\w+|unveil\w+|drops?|bans?"
    r"|sues?|warns?|raises?|kills?|leak\w+|fires?|acqui\w+|report\w+)\b",
    re.IGNORECASE)


def choose_format(topic: str, key_points: str = "") -> str:
    """Pick the carousel format for a topic. Free, deterministic, no GPT.

    Priority listicle > tutorial > news_hybrid: a numbered-tools topic often
    also contains 'use'/'make' words, and a how-to phrasing beats a stray
    news verb. Default = tutorial (the brand's #1 format)."""
    text = f"{topic} {key_points}"
    if _LISTICLE_PAT.search(text):
        return "listicle"
    if _TUTORIAL_PAT.search(text):
        return "tutorial"
    if _NEWS_PAT.search(text):
        return "news_hybrid"
    return "tutorial"


# ---------------------------------------------------------------------------
# FREE: skeleton spec — right slide mix, TODO copy
# ---------------------------------------------------------------------------
def _expand_plan(fmt: str) -> list[dict]:
    """Expand a format's slide_plan into concrete slide stubs (repeating
    STEP/TOOL slides use the LOW end of their n-range; the drafter may add
    more up to the high end)."""
    out = []
    for entry in FORMATS[fmt]["slide_plan"]:
        lo, _hi = entry.get("n", (1, 1))
        for i in range(1, lo + 1):
            e = {k: v for k, v in entry.items() if k != "n"}
            if "{n}" in e.get("kicker", ""):
                e["kicker"] = e["kicker"].replace("{n}", str(i))
            out.append(e)
    return out


def skeleton_spec(topic: str, fmt: str | None = None) -> dict:
    """A fill-in-the-blanks spec with the format's slide mix. Free."""
    fmt = fmt or choose_format(topic)
    slug = slugify(topic)[:40]
    slides = []
    for i, e in enumerate(_expand_plan(fmt), start=1):
        s: dict = {"type": e["type"]}
        if e.get("kicker"):
            s["kicker"] = e["kicker"]
        if e["type"] in ("cover", "content"):
            s["headline"] = "TODO"
            s["neon_word"] = "TODO"
            s["media_keywords"] = [f"{slug}_{i}", "TODO-visual-word"]
            if e["type"] == "cover":
                s["subheadline"] = "TODO"
                s["counter_value"] = "TODO"
                s["counter_label"] = "TODO"
            else:
                s["body"] = "TODO"
                s["ref_query"] = "TODO clean visual search phrase"
        elif e["type"] == "recap":
            s["headline"] = "What you just learned"
            s["neon_word"] = "learned"
            s["points"] = ["TODO", "TODO", "TODO"]
        elif e["type"] == "cta":
            s["headline"] = "Follow For Free"
            s["pills"] = ["DAILY AI TOOLS & HOW-TOS",
                          "FRONTIER AI, EXPLAINED SIMPLY",
                          "NO FLUFF. NO HYPE."]
        if e.get("note"):
            s["_note"] = e["note"]
        slides.append(s)
    return {
        "topic": topic,
        "format": fmt,
        "caption": {"hook": "TODO", "summary": "TODO", "content": "TODO",
                    "cta": ("Save this + follow @genzcapital for daily AI "
                            "tools & how-tos. No fluff. No hype."),
                    "hashtags": ["#ai", "#aitools", "#aitips",
                                 "#artificialintelligence", "#genzcapital"]},
        "slides": slides,
    }


# ---------------------------------------------------------------------------
# Validation — catches a bad GPT draft (or hand edit) BEFORE a paid image run
# ---------------------------------------------------------------------------
def validate_spec(spec: dict) -> list[str]:
    """Return a list of problems ([] = good)."""
    problems = []
    if not spec.get("topic"):
        problems.append("missing topic")
    if spec.get("format") not in VALID_FORMATS:
        problems.append(f"format must be one of {VALID_FORMATS}")
    cap = spec.get("caption") or {}
    for k in ("hook", "summary", "content", "cta"):
        if "TODO" in str(cap.get(k, "TODO")):
            problems.append(f"caption.{k} is missing/TODO")
    slides = spec.get("slides") or []
    if not slides or slides[0].get("type") != "cover":
        problems.append("slide 1 must be a cover")
    # IG carousel cap is 10; we add 1 caption card on top of the slides.
    if len(slides) + 1 > 10:
        problems.append(f"{len(slides)} slides + caption card > 10 (IG cap)")
    for i, s in enumerate(slides, start=1):
        t = s.get("type")
        if t in ("cover", "content"):
            hl = s.get("headline", "")
            if not hl or "TODO" in hl:
                problems.append(f"slide {i}: headline missing/TODO")
            nw = s.get("neon_word", "")
            if nw and nw.lower() not in hl.lower():
                problems.append(
                    f"slide {i}: neon_word {nw!r} not in headline")
            if any("TODO" in str(v) for v in s.values()):
                problems.append(f"slide {i}: has TODO fields")
        if t == "content" and not s.get("body"):
            problems.append(f"slide {i}: content slide needs a body")
    return problems


# ---------------------------------------------------------------------------
# PAID: GPT fills the template into a full spec (cheap chat call, guarded)
# ---------------------------------------------------------------------------
_BRAND_VOICE = (
    "Brand: Gen Z Capital — AI tools & how-tos for a Gen Z audience. "
    "Voice: direct, serious, zero hype, zero emojis ON SLIDES (the caption "
    "MAY use 1-2). Short sentences. Every post must leave the reader with "
    "something they can actually USE today."
)

_SCHEMA_RULES = (
    "Output STRICT JSON matching the skeleton you are given: same slide "
    "order and types; you may add extra STEP/TOOL content slides up to the "
    "format's max. Replace every TODO. Remove every '_note' key.\n"
    "- headline: SENTENCE case (verified @evolving.ai style — the renderer "
    "uppercases the cover itself), <= 9 words; neon_word = ONE word that "
    "appears verbatim in that headline (the renderer paints it neon green).\n"
    "- cover subheadline: <= 14 words, the hook's consequence (rendered small "
    "ALL-CAPS at the bottom of the cover).\n"
    "- kicker: keep the skeleton's kicker (STEP 2, TOOL 3, ...).\n"
    "- body: <= 40 words, concrete.\n"
    "- media_keywords: keep item 1 (the slide-id filename) exactly as given; "
    "replace item 2 with ONE lowercase visual word for the slide.\n"
    "- ref_query (content slides only): a clean 3-6 word VISUAL search "
    "phrase (what a photo of this slide looks like), never marketing copy.\n"
    "- The cover gets NO ref_query (the pipeline auto-derives person+brand).\n"
    "- caption: hook (1 line), summary (2-3 lines), content (the value, may "
    "use line breaks and • bullets), cta (keep as given), hashtags (6-8, "
    "topical).\n"
)

_FACTS_RULE = (
    "HARD FACT RULE: you may ONLY state facts that appear in the TOPIC, KEY "
    "POINTS, or SOURCE text below. Never invent numbers, quotes, dates, "
    "names, features or prices. If a specific is not provided, write around "
    "it or attribute it ('reportedly'). Breaking this rule ruins the brand."
)


def draft_spec(topic: str, *, fmt: str | None = None, key_points: str = "",
               source_text: str = "",
               guard: UsageGuard | None = None) -> dict:
    """One cheap chat call: GPT fills the format skeleton with real copy.
    Costs money (~$0.001-0.01) — caller is responsible for user confirmation.
    """
    from openai import OpenAI
    load_dotenv(REPO_ROOT / ".env")
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not set in .env")
    client = OpenAI(api_key=key)
    model = os.getenv("OPENAI_SPEC_MODEL", "gpt-4o-mini")

    fmt = fmt or choose_format(topic, key_points)
    skel = skeleton_spec(topic, fmt)
    f = FORMATS[fmt]

    prompt = (
        f"{_BRAND_VOICE}\n\n"
        f"FORMAT: {f['label']} — {f['goal']}\nFORMAT RULES: {f['rules']}\n\n"
        f"{_FACTS_RULE}\n\nTOPIC: {topic}\n"
        + (f"KEY POINTS: {key_points}\n" if key_points else "")
        + (f"SOURCE:\n{source_text}\n" if source_text else "")
        + f"\n{_SCHEMA_RULES}\nSKELETON:\n{json.dumps(skel, indent=2)}"
    )
    resp = client.chat.completions.create(
        model=model, temperature=0.7,
        response_format={"type": "json_object"},
        messages=[{"role": "user", "content": prompt}],
    )
    if guard is not None:
        guard.register_chat_usage(getattr(resp, "usage", None))
    spec = json.loads(resp.choices[0].message.content)
    spec["topic"] = topic          # never let the model rewrite the topic
    spec["format"] = fmt
    for s in spec.get("slides", []):
        s.pop("_note", None)
        if s.get("type") == "cover":
            s.pop("ref_query", None)   # cover query is auto (topic_keywords)
    problems = validate_spec(spec)
    if problems:
        log.warning("Draft has %d problem(s): %s", len(problems), problems)
        spec["_validation"] = problems
    return spec


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--topic", required=True)
    ap.add_argument("--format", choices=VALID_FORMATS, default=None,
                    help="force a format (default: auto from the topic)")
    ap.add_argument("--key-points", default="", help="facts GPT may use")
    ap.add_argument("--source", default="",
                    help="path to a text file with source material (facts)")
    ap.add_argument("--skeleton", action="store_true",
                    help="FREE: write the TODO skeleton only, no GPT")
    ap.add_argument("--out", default=None,
                    help="output spec path (default assets/carousels/"
                         "<slug>_spec.json)")
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing spec file")
    args = ap.parse_args()

    fmt = args.format or choose_format(args.topic, args.key_points)
    log.info("Topic -> format: %s (%s)", fmt, FORMATS[fmt]["label"])

    if args.skeleton:
        spec = skeleton_spec(args.topic, fmt)
    else:
        guard = UsageGuard.from_env(str(REPO_ROOT))
        guard.start_run()
        source_text = (Path(args.source).read_text(encoding="utf-8")
                       if args.source else "")
        spec = draft_spec(args.topic, fmt=fmt, key_points=args.key_points,
                          source_text=source_text, guard=guard)
        snap = guard.snapshot()
        log.info("Draft cost: $%.4f", snap.run_usd)

    out = Path(args.out) if args.out else (
        SPECS_DIR / f"{slugify(args.topic)[:40]}_spec.json")
    if not out.is_absolute():
        out = REPO_ROOT / out
    if out.exists() and not args.force:
        raise SystemExit(f"{out} exists — pass --force to overwrite")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(spec, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    log.info("Spec written: %s", out.relative_to(REPO_ROOT))

    problems = spec.get("_validation") or (
        validate_spec(spec) if not args.skeleton else [])
    if problems:
        log.warning("Fix before building: %s", problems)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
