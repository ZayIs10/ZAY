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
        # LAUNCH FORMAT — locked 2026-06-19. The flagship first carousel type.
        # Full rationale + decisions: brainstorms/evolving-ai-essentials-to-copy.md
        # and docs/evolving_ai_carousel_format.md. Copy @evolving.ai's ENGINE
        # (1 image/slide, product logo every slide, 1 idea/slide, source-cited
        # facts, swipe-for-more) but FLIP the purpose: they REPORT, we TEACH a
        # usable skill + build the "unfair AI advantage" mindset.
        "label": "Tutorial / How-to",
        "goal": ("Teach ONE usable AI skill end-to-end. The reader must be "
                 "able to DO the thing after swiping — exact tool, exact "
                 "clicks, exact prompt text where relevant. The viewer is the "
                 "protagonist; the AI tool is the weapon we hand them."),
        "slide_plan": [
            {"type": "cover", "kicker": "AI HOW-TO",
             "note": "HOOK ON THE VIEWER'S STAKES, never AI spectacle. DEFAULT "
                     "hook = 'outcome in X steps' (e.g. 'Build an AI app in 4 "
                     "steps'), so the headline names the RESULT + the step "
                     "count. Rotation variants when they fit better: 'You're "
                     "using <tool> wrong' (pattern-interrupt) or 'The <tool> "
                     "trick nobody's using' (FOMO/unfair-advantage). counter "
                     "chip = effort/cost (e.g. FREE / 2 MIN / 4 STEPS)."},
            {"type": "content", "kicker": "STEP {n}", "n": (3, 5),
             "note": "ONE concrete action per slide = one numbered focal "
                     "point, never two ideas on a slide. Body says exactly "
                     "what to open/click/type. If a prompt is part of the "
                     "step, include the literal prompt text. If the step "
                     "states a fact/stat, add a 'source' tag (see source "
                     "field)."},
            {"type": "content", "kicker": "PRO TIP",
             "note": "One non-obvious upgrade to the basic steps."},
            {"type": "recap", "note": "Re-list every step, one line each. This "
                     "is the SAVE ENGINE — it is why a how-to gets bookmarked. "
                     "Non-negotiable; never drop it."},
            {"type": "cta", "note": "Phase-1 CTA: name the SPECIFIC ongoing "
                     "value of following ('I find one of these every day so "
                     "you don't have to'). NEVER bare 'follow for more' (IG "
                     "engagement-bait flag + weak). No link CTA until Phase 2."},
        ],
        "rules": ("Steps must be concrete and reproducible. No vague advice "
                  "('experiment with settings'). Name the actual tool/menu/"
                  "button. Free options first. Cover hook = 'outcome in X "
                  "steps' by default. A content slide that states a fact/stat "
                  "MUST carry a 'source' tag (where the fact came from); a "
                  "pure how-to action slide needs no source. v1 images are "
                  "AI-generated cinematic; one numbered focal point per slide."),
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

    TUTORIAL IS THE LOCKED DEFAULT (user lock, 2026-06-27): the user wants the
    teaches-a-skill, "outcome in X steps" tutorial format on EVERY carousel
    unless a topic EXPLICITLY asks for another format. So tutorial wins for any
    topic that isn't an unmistakable listicle or pure news:
      - listicle ONLY when the topic is unmistakably a numbered round-up
        (e.g. "5 AI tools", "top 7 ...") — _LISTICLE_PAT;
      - news_hybrid ONLY when the topic is clearly breaking news AND carries no
        how-to signal at all (so a "how to use X's new feature" stays tutorial);
      - everything else -> tutorial.
    To force a format regardless of the topic, pass --format on the CLI or set
    the Sheet's Format column (draft_from_sheet honours an explicit Format).
    """
    text = f"{topic} {key_points}"
    if _LISTICLE_PAT.search(text):
        return "listicle"
    # Pure breaking-news ONLY when there is no how-to/skill phrasing — a topic
    # like "How to use Claude's new feature" keeps tutorial even though it has a
    # news verb. Tutorial is the default for literally everything else.
    if _NEWS_PAT.search(text) and not _TUTORIAL_PAT.search(text):
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


def _repeat_range(fmt: str) -> tuple[int, int]:
    """The (min, max) count of the format's repeating STEP/TOOL content slide —
    how far the drafter may flex slide count to fit the content. Formats with no
    repeating slide (e.g. news_hybrid's fixed beats) report (1, 1)."""
    for entry in FORMATS[fmt]["slide_plan"]:
        if "n" in entry:
            return entry["n"]
    return (1, 1)


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
                # OPTIONAL source tag — only on slides that state a fact/stat.
                # The drafter fills it (e.g. "OpenAI") or drops it for pure
                # how-to action steps. Rendered small in the slide corner.
                s["source"] = ""
        elif e["type"] == "recap":
            s["headline"] = "What you just learned"
            s["neon_word"] = "learned"
            s["points"] = ["TODO", "TODO", "TODO"]
        elif e["type"] == "cta":
            # Phase-1 CTA (locked): name the SPECIFIC ongoing value of
            # following — never bare "follow for more". No link until Phase 2.
            s["headline"] = "I find these so you don't have to"
            s["neon_word"] = "you"
            s["pills"] = ["ONE USABLE AI SKILL A DAY",
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
        # CTA must NOT be bare "follow for more" (IG engagement-bait flag).
        if t == "cta":
            hl = (s.get("headline") or "").lower()
            if re.search(r"follow\s+for\s+more|like\s+and\s+subscribe", hl):
                problems.append(
                    f"slide {i}: CTA uses bare 'follow for more'/'like and "
                    f"subscribe' — name the specific value of following")
    # Tutorial decks REQUIRE a recap slide — it is the save engine (locked).
    if spec.get("format") == "tutorial":
        types = [s.get("type") for s in slides]
        if "recap" not in types:
            problems.append("tutorial is missing its recap slide (the save "
                            "engine — required, never drop it)")
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
    "order and types. Replace every TODO. Remove every '_note' key.\n"
    "SLIDE COUNT — DECIDE IT FROM THE CONTENT, do not just keep the skeleton "
    "count: ONE distinct step / tool / idea per content slide, no more, no "
    "less. If the topic naturally has 6 steps, output 6 STEP slides; if it "
    "has 3, output 3 — never pad with filler and never cram two ideas onto "
    "one slide. Add or remove the repeating STEP/TOOL content slides to match "
    "(stay within the {n_lo}-{n_hi} the format allows, and the whole deck "
    "must be <= 8 slides so a caption card keeps it under Instagram's 10). "
    "Keep the cover, recap and cta exactly once each.\n"
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
    "- source (content slides only): if the slide STATES A FACT/STAT/CLAIM, "
    "set source to where it came from (e.g. 'OpenAI', 'Anthropic', the report "
    "name) — a credibility tag rendered in the slide corner. For a pure "
    "how-to ACTION step (open X, click Y, type Z) leave source as \"\" (empty) "
    "and the renderer shows nothing. Never invent a source.\n"
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

    # The repeating STEP/TOOL slide's allowed count range, so the drafter knows
    # how far it may flex the slide count to fit the content (auto-from-content).
    n_lo, n_hi = _repeat_range(fmt)
    schema_rules = _SCHEMA_RULES.format(n_lo=n_lo, n_hi=n_hi)

    prompt = (
        f"{_BRAND_VOICE}\n\n"
        f"FORMAT: {f['label']} — {f['goal']}\nFORMAT RULES: {f['rules']}\n\n"
        f"{_FACTS_RULE}\n\nTOPIC: {topic}\n"
        + (f"KEY POINTS: {key_points}\n" if key_points else "")
        + (f"SOURCE:\n{source_text}\n" if source_text else "")
        + f"\n{schema_rules}\nSKELETON:\n{json.dumps(skel, indent=2)}"
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
# AUTO-DRAFT FROM THE SHEET — the missing link that completes the pipeline
# ---------------------------------------------------------------------------
# Flow (carousel sibling of the reels claim->build loop):
#   row Status == "Ready to Run"  (user sets this)
#     -> THIS picks the row, chooses the format (default tutorial = locked
#        launch format), drafts a spec with draft_spec (PAID GPT text call,
#        usage-guarded), validates it, writes assets/carousels/<slug>_spec.json
#     -> prints the spec path so the GitHub workflow dispatches build_carousel
#        on it -> images -> render -> Drive -> review email -> "Ready to Post".
#
# DRY RUN does EVERYTHING except the paid draft: it reads the Sheet, picks the
# row + format, and writes a FREE skeleton spec instead — so the whole chain is
# verifiable with zero API spend (the user's "set everything up but don't fire
# images yet" rule).
TRIGGER_STATUS = "ready to run"   # matched trimmed + lowercased (reels parity)
DRAFTING_STATUS = "Drafting"      # claim word: distinct from build's "Building"


def _carousel_rows():
    """(reader, [row dicts]) for the carousel Sheet — reuses the review
    module's reader so there is ONE Sheet-access path, not two."""
    from publisher.carousel_review import _carousel_sheet_reader
    reader = _carousel_sheet_reader()
    values = reader.ws.get_all_values()
    if not values:
        return reader, []
    headers = values[0]
    rows = []
    for i, raw in enumerate(values[1:], start=2):
        row = {headers[j]: (raw[j] if j < len(raw) else "")
               for j in range(len(headers))}
        row["_row_index"] = i
        rows.append(row)
    return reader, rows


def _is_carousel_row(row: dict) -> bool:
    """Carousels share the Reels tab; a 'Post Type' == 'carousel' marks them
    (the existing project convention — see batch_generate / post_generator).
    Rows with no Post Type are treated as carousels ONLY on a dedicated tab;
    on the shared tab a blank Post Type is ambiguous, so we require the tag."""
    return str(row.get("Post Type", "")).strip().lower() == "carousel"


def next_ready_topic() -> dict | None:
    """Return the first carousel row whose Status == 'Ready to Run', or None.
    Free (one Sheet read). Carousels share the Reels tab, so we ALSO require
    Post Type == 'carousel' to avoid grabbing a reel row. Picks by Status,
    addresses by Topic downstream."""
    _reader, rows = _carousel_rows()
    for row in rows:
        if str(row.get("Status", "")).strip().lower() != TRIGGER_STATUS:
            continue
        if not _is_carousel_row(row):
            continue  # a reel row on the shared tab — leave it for the reel flow
        return row
    return None


def draft_from_sheet(*, dry_run: bool = False,
                     out_dir: Path | None = None) -> dict:
    """Find the next 'Ready to Run' carousel row and turn it into a spec file.

    Returns {"topic", "format", "spec_path", "drafted": bool, "problems": [...]}
    or {"topic": None} when no row is ready.

    dry_run=True  -> FREE skeleton spec (no GPT, no spend) — proves the chain.
    dry_run=False -> PAID draft_spec (cheap GPT text call, usage-guarded).
    """
    row = next_ready_topic()
    if not row:
        log.info("No carousel row with Status == 'Ready to Run'.")
        return {"topic": None}

    topic = str(row.get("Topic", "")).strip()
    key_points = str(row.get("Key Points", "") or row.get("Key Points / Notes",
                                                          "")).strip()
    # Format: honour an explicit "Format" column if present + valid, else the
    # free classifier (which defaults to tutorial = our locked launch format).
    col_fmt = str(row.get("Format", "")).strip().lower()
    fmt = col_fmt if col_fmt in VALID_FORMATS else choose_format(topic,
                                                                 key_points)

    out_dir = out_dir or SPECS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    spec_path = out_dir / f"{slugify(topic)[:40]}_spec.json"

    if dry_run:
        spec = skeleton_spec(topic, fmt)
        problems = []  # skeleton is intentionally TODO-filled; not validated
        drafted = False
        log.info("[dry-run] row %s -> FREE skeleton (%s), no GPT spend.",
                 row.get("_row_index"), fmt)
    else:
        guard = UsageGuard.from_env(str(REPO_ROOT))
        guard.start_run()
        spec = draft_spec(topic, fmt=fmt, key_points=key_points, guard=guard)
        problems = spec.get("_validation", [])
        drafted = True
        log.info("Drafted spec for %r (%s); cost $%.4f",
                 topic, fmt, guard.snapshot().run_usd)

    spec_path.write_text(json.dumps(spec, indent=2, ensure_ascii=False),
                         encoding="utf-8")
    log.info("Spec written: %s", spec_path.relative_to(REPO_ROOT))
    return {
        "topic": topic, "format": fmt,
        "spec_path": str(spec_path.relative_to(REPO_ROOT)),
        "drafted": drafted, "problems": problems,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    # Subcommand: draft the next Ready-to-Run Sheet row into a spec.
    if len(sys.argv) > 1 and sys.argv[1] == "from-sheet":
        sub = argparse.ArgumentParser(
            prog="carousel_templates.py from-sheet",
            description="Draft the next 'Ready to Run' carousel row into a "
                        "spec. --dry-run writes a FREE skeleton (no spend).")
        sub.add_argument("--dry-run", action="store_true",
                         help="FREE skeleton instead of the paid GPT draft")
        sub.add_argument("--github-output", action="store_true",
                         help="also emit spec_path/topic to $GITHUB_OUTPUT "
                              "for the workflow to consume")
        a = sub.parse_args(sys.argv[2:])
        res = draft_from_sheet(dry_run=a.dry_run)
        print(json.dumps(res, indent=2, ensure_ascii=False))
        if a.github_output and res.get("spec_path"):
            gh_out = os.getenv("GITHUB_OUTPUT")
            if gh_out:
                with open(gh_out, "a", encoding="utf-8") as fh:
                    fh.write(f"spec_path={res['spec_path']}\n")
                    fh.write(f"topic={res.get('topic', '')}\n")
                    fh.write(f"drafted={str(res.get('drafted')).lower()}\n")
        return 0

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
