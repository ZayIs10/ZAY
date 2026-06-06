"""Decide whether a topic should become a REEL or a CAROUSEL post.

Not every topic suits a reel. The user's feedback (2026-06-06): daily-news
bites and "AI is improving itself" trend commentary are NOT entertaining as
reels — they're just a text card over b-roll. How-to / multi-step / tool-
comparison topics work better as a swipeable carousel the viewer pauses to
read. This module is the qualification gate that tags each topic.

The split is modelled on @evolving.ai (see the reference + knowledge-base
memories):

  REEL  (1080x1920, ~30s, one clip + text reveal):
    - ONE punchy insight that can be *felt* in 30s with one visual demo.
    - Single news story / launch / one jaw-dropping number or moment.
    - Hook -> problem -> insight -> proof -> CTA, no pausing required.

  CAROUSEL  (1080x1350, multi-slide, swipe to read):
    - Multi-STEP how-tos ("how to use X", "5 steps to...").
    - Tool comparisons / "best N tools for ..." lists.
    - Prompt libraries, cheat-sheets, frameworks — anything the viewer
      must PAUSE and READ. Value can't be felt in 30s.

Two-stage design so it's cheap and predictable:
  1. Deterministic keyword guardrail catches the obvious cases for free
     (a "how to" / "best 7 tools" topic is clearly a carousel; a single
     "X just launched Y" launch is clearly a reel).
  2. Only the ambiguous middle falls through to a single GPT classification
     call. If GPT is unavailable, we degrade to a keyword-only heuristic so
     research never hard-fails on this step.
"""

from __future__ import annotations

import json
import logging
import re

log = logging.getLogger("format_classifier")

REEL = "reel"
CAROUSEL = "carousel"

# ---------------------------------------------------------------------------
# Stage 1 — deterministic guardrail
# ---------------------------------------------------------------------------
# Strong signals a topic is a multi-step / pause-and-read piece -> CAROUSEL.
# These mirror exactly what @evolving.ai puts in carousels vs reels.
_CAROUSEL_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"\bhow to\b", re.I),
    re.compile(r"\bstep[\s-]?by[\s-]?step\b", re.I),
    re.compile(r"\b\d+\s+(steps?|ways?|tips?|tricks?|prompts?|tools?|hacks?|"
               r"reasons?|mistakes?|examples?|templates?|frameworks?)\b",
               re.I),
    re.compile(r"\bbest\s+[\w\s]{0,12}?(tools?|apps?|prompts?|ways?)\b", re.I),
    re.compile(r"\b(top|ultimate)\s+\d+\b", re.I),
    re.compile(r"\bvs\.?\b|\bversus\b|\bcompared?\s+to\b", re.I),
    re.compile(r"\bcomparison\b|\bcheat[\s-]?sheet\b|\bchecklist\b", re.I),
    re.compile(r"\bguide\b|\btutorial\b|\bworkflow\b|\bframework\b", re.I),
    re.compile(r"\bprompt(s)?\s+(library|pack|to)\b", re.I),
    re.compile(r"\bbeginner'?s?\b|\bfor beginners\b", re.I),
)

# Strong signals a topic is a single punchy moment / news bite -> REEL.
_REEL_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"\bjust\s+(launched|shipped|released|dropped|announced|"
               r"unveiled|raised|hit|bet|acquired|open[\s-]?sourced)\b", re.I),
    re.compile(r"\b(launches?|launched|unveils?|unveiled|releases?|released|"
               r"announces?|announced|debuts?)\b", re.I),
    re.compile(r"\b\$\d", re.I),                         # a dollar figure
    re.compile(r"\b\d+\s*(million|billion|m|b|k|x)\b", re.I),  # a big number
    re.compile(r"\bnow\b.*\b(can|is|does|writes|builds|codes)\b", re.I),
    re.compile(r"\bbreaks?\b|\bbeats?\b|\bcrushes?\b|\bshocks?\b", re.I),
)


def _guardrail(text: str) -> str | None:
    """Return REEL/CAROUSEL if a deterministic signal fires, else None.

    Carousel signals win ties: a "how to use the model OpenAI just launched"
    is a multi-step teach -> carousel. Pause-and-read beats single-moment.
    """
    carousel_hit = any(p.search(text) for p in _CAROUSEL_PATTERNS)
    reel_hit = any(p.search(text) for p in _REEL_PATTERNS)
    if carousel_hit:
        return CAROUSEL
    if reel_hit:
        return REEL
    return None


# ---------------------------------------------------------------------------
# Stage 2 — GPT classifier for the ambiguous middle
# ---------------------------------------------------------------------------
_GPT_SYSTEM = (
    "You are a content-format editor for a faceless AI-skills channel "
    "modelled on @evolving.ai. You decide whether a topic should be a "
    "short-form REEL or a swipeable CAROUSEL post. Output VALID JSON only."
)

_GPT_USER = """Topic: {topic}
Key points: {key_points}

Decide the best format using the @evolving.ai split:

REEL (1080x1920, ~30 seconds, one background clip + a text reveal):
- ONE punchy insight, launch, or jaw-dropping moment the viewer can FEEL in
  30 seconds with a single visual demo.
- A single news story: "X just launched Y", "model Z hit N%", one big number.
- No pausing or reading required to get the value.

CAROUSEL (1080x1350, multiple slides the viewer swipes and reads):
- Multi-STEP how-tos ("how to use X", "5 steps to automate Y").
- Tool comparisons or "best N tools/apps/prompts for ..." lists.
- Prompt libraries, cheat-sheets, frameworks, checklists.
- Anything where the value requires PAUSING to read across several slides.

If the value cannot be felt in 30 seconds without pausing, it is a CAROUSEL.
Plain daily-news commentary with no single visual demo is a weak reel —
prefer CAROUSEL unless there is one clear thing to SHOW happening.

Return EXACTLY this JSON shape (format is "reel" or "carousel",
confidence is 0.0-1.0, reason is one short sentence):
{{"format": "reel", "confidence": 0.0, "reason": "..."}}
"""


def _classify_with_gpt(
    client, topic: str, key_points: str, model: str,
) -> dict | None:
    try:
        resp = client.chat.completions.create(
            model=model,
            temperature=0.0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _GPT_SYSTEM},
                {"role": "user", "content": _GPT_USER.format(
                    topic=topic, key_points=key_points or "(none)",
                )},
            ],
        )
        data = json.loads(resp.choices[0].message.content)
        fmt = str(data.get("format", "")).strip().lower()
        if fmt not in (REEL, CAROUSEL):
            return None
        return {
            "format": fmt,
            "confidence": float(data.get("confidence", 0.5)),
            "reason": str(data.get("reason", "")).strip(),
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("GPT format classification failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def classify_format(
    topic: str,
    key_points: str = "",
    client=None,
    model: str = "gpt-4o-mini",
) -> dict:
    """Return {format, confidence, reason, source} for a topic.

    - `format` is "reel" or "carousel".
    - `source` is "guardrail" (deterministic keyword) or "gpt" or "fallback".
    Never raises: if GPT is unavailable it degrades to the keyword heuristic,
    defaulting to REEL only for clearly single-moment topics and CAROUSEL
    otherwise (a multi-step teach should not be forced into a 30s reel).
    """
    text = f"{topic} {key_points}".strip()

    hit = _guardrail(text)
    if hit is not None:
        return {
            "format": hit,
            "confidence": 0.9,
            "reason": "Matched a deterministic format keyword.",
            "source": "guardrail",
        }

    if client is not None:
        result = _classify_with_gpt(client, topic, key_points, model)
        if result is not None:
            result["source"] = "gpt"
            return result

    # Fallback: no keyword fired and no GPT. A topic with no single "launch /
    # number / moment" signal is more likely a teach -> default CAROUSEL so we
    # don't ship a boring text-only reel. (The guardrail already routed the
    # obvious single-moment reels above.)
    return {
        "format": CAROUSEL,
        "confidence": 0.4,
        "reason": "No single-moment signal and no classifier available; "
                  "defaulting to carousel to avoid a text-only reel.",
        "source": "fallback",
    }


if __name__ == "__main__":  # quick manual check
    logging.basicConfig(level=logging.INFO)
    samples = [
        ("Anthropic just launched Claude Opus 4.8", ""),
        ("How to use Claude to write your emails in 5 steps", ""),
        ("Best 7 AI tools to replace your assistant", ""),
        ("ChatGPT vs Gemini for coding", ""),
        ("Gates bet $200M on Anthropic", ""),
        ("AI is getting better at reasoning", "general trend commentary"),
        ("The 3 prompts that make ChatGPT 10x faster", ""),
    ]
    for t, kp in samples:
        r = classify_format(t, kp)  # no client -> guardrail/fallback only
        print(f"[{r['format']:8}] ({r['source']:9}) {t}")
