"""cover_director.py — FLEXIBLE cover-image art direction for Gen Z Capital.

The brand owner's note: the cover MUST stop the scroll and the image must
obviously match the topic — but it must NOT be one rigid "single object on
black" formula. @evolving.ai's covers RANGE (a rich film-set scene, a real CEO,
a cinematic silhouette, a symbolic object); their TOP post was a clean
silhouette, proving variety itself is the edge.

So this module picks ONE of several cover ARCHETYPES per topic and builds the
gpt-image-1 prompt for it. It reuses signals the pipeline already computes
(topic_keywords -> brands/person, the format classifier) and writes the prompt
into beat['prompt'], which the pipeline already treats as a vision-skip override.

Researched 2026-06-14 (competitor attention psychology + @evolving.ai's real
live covers). See memory [[feedback_cover_image_must_match_topic]] and
[[cover_director_archetypes]].

Universal rules baked into EVERY prompt:
  - subject in the TOP ~58% of the 4:5 frame; bottom 40% clean near-black for
    the renderer's headline (the 60/40 split).
  - exactly ONE dominant focal point (1-3 elements max; 3+ elements = -23% CTR).
  - high contrast: near-black base + ONE saturated accent rim (neon-green
    #39FF14 default, RED for danger topics).
  - zero text/letters/UI/logos/numbers in the image (renderer owns all text).
  - faces/hands must be photographic, not uncanny plastic.
"""
from __future__ import annotations

import re

NEON = "neon-green (#39FF14)"
RED = "menacing red (#FF2A2A)"

# Shared trailer appended to every archetype prompt — the non-negotiable rules.
_FRAMING = (
    "Vertical 4:5 composition. CRITICAL: place the subject in the TOP 58% of "
    "the frame; the BOTTOM 40% must be clean, dark, near-black EMPTY negative "
    "space (a headline is overlaid there) — the subject must be fully visible "
    "above it, not cropped. Exactly one dominant focal point, minimal clutter. "
    "Ultra-realistic, cinematic, shot on 35mm, real texture and grain, NOT a "
    "plastic 3D render, not uncanny. Absolutely NO text, no words, no letters, "
    "no numbers, no captions, no logos, no UI, no watermark anywhere."
)

# --- signal regexes (cheap, free) ------------------------------------------
_DANGER = re.compile(
    r"\b(ban|banned|leak|leaked|sued|lawsuit|warn|warns|warning|risk|risky|"
    r"danger|dangerous|threat|threaten|fired|laid ?off|layoff|shutdown|shut "
    r"down|breach|hacked|scam|crash|kill|deadly|too (risky|dangerous|powerful))",
    re.I)
_MONEY = re.compile(
    r"(\$|\bbillion|\bmillion|\btrillion|raised|valuation|valued|worth|funding|"
    r"revenue|profit|\bIPO\b|layoff|cut \d+|\d+ ?(jobs|roles))", re.I)
_COMPARE = re.compile(
    r"(\bvs\b|versus|compared|comparison|replaces|replaced|killed|kills|"
    r"old way|new way|\b\d+ ?x\b|upgrade|before|after|beats)", re.I)
_TOOL = re.compile(
    r"\b(how to|use|using|build|building|create|creating|prompt|generate|"
    r"workflow|step|tutorial|make|automate)\b", re.I)
_ACTOR = re.compile(
    r"\b(say|says|said|warn|warns|warned|announce|announced|launch|launched|"
    r"fire|fired|admit|admitted|order|ordered|reveal|revealed|claim|claims|"
    r"unveil|unveiled|drop|drops|dropped|ship|ships|shipped)\b", re.I)
_FUTURE = re.compile(
    r"\b(future|will|coming|soon|era|changes everything|change everything|"
    r"world|humanity|forever|next|dawn|age of)\b", re.I)


def _accent(topic: str) -> str:
    """Danger topics override brand-green with red; else neon-green."""
    return RED if _DANGER.search(topic) else NEON


def _seeded(topic: str, options: list[str]) -> str:
    """Deterministic-but-varied pick from a list, seeded by the topic string —
    so consecutive posts differ (anti-samey) but a given topic is stable across
    re-renders. No Math.random (would break reproducibility)."""
    if not options:
        return ""
    h = sum(ord(c) for c in topic)
    return options[h % len(options)]


# --- archetype prompt builders ---------------------------------------------
def _ceo_face(subject, person, accent, topic):
    angle = _seeded(topic, ["eye-level, looking just off camera",
                            "slight low hero angle, looking toward camera",
                            "three-quarter profile, focused gaze"])
    return (
        f"A recognizable real public figure, {person}, head and shoulders, "
        f"{angle}. A subtle high-intent micro-expression — focused, determined "
        f"or concerned, mouth closed (not cartoonish shock). A {accent} rim "
        f"light from one side separating them from a near-black background. "
        f"Real skin texture, photographic. {_FRAMING}")


def _symbolic_object(subject, accent, topic):
    obj = _seeded(topic, [
        "a single glowing neural orb of light",
        "one sleek monolithic AI core",
        "a single sculptural porcelain android head, serene",
        "one cracked padlock of dark glass",
    ])
    return (
        f"ONE dramatic symbolic object representing '{subject}': {obj}. The "
        f"object centered, ~35% of frame, dramatic key light with a {accent} "
        f"edge glow, deep shallow depth of field, volumetric haze, floating in "
        f"a near-black void. Strictly one focal object, no clutter. {_FRAMING}")


def _product_hero(subject, accent, topic):
    return (
        f"A stylized hero shot for '{subject}': a glowing laptop or device "
        f"screen lit on a dark desk showing an abstract glowing interface (no "
        f"readable text), a real human hand just entering frame toward it. "
        f"Screen glow tinted {accent}, soft bright highlight on the hero area. "
        f"{_FRAMING}")


def _before_after(subject, accent, topic):
    return (
        f"A single vertical split image showing a transformation for "
        f"'{subject}', contained in the TOP 58% only (do NOT split the bottom "
        f"band). LEFT/old: desaturated, dim, muted, slightly messy. RIGHT/new: "
        f"saturated, sharp, {accent}-lit, clean. The SAME subject on both sides "
        f"so the change reads instantly. One clear seam, two states, nothing "
        f"else. {_FRAMING}")


def _staged_scene(subject, accent, topic):
    setting = _seeded(topic, [
        "a colossal dark monolithic AI server-cathedral glowing from within "
        "its seams",
        "a vast neon-lit futuristic control room",
        "a towering sci-fi structure at golden hour",
        "a lone figure facing an enormous glowing screen in a dark hall",
    ])
    return (
        f"A cinematic environmental scene for '{subject}': ONE lone human "
        f"silhouette seen from behind, small, for scale and emotion (not a "
        f"recognizable face), before {setting}. {accent} light from within, "
        f"thin volumetric haze, ominous scale, the figure dwarfed and still. "
        f"Strong single light source, shallow depth of field. {_FRAMING}")


def _money_hero(subject, accent, topic):
    sym = _seeded(topic, [
        "towering dramatically-lit stacks of cash and gold",
        "a glowing upward chart-curve rendered as a ribbon of light",
        "a vast vault door",
        "a rising skyline of glowing skyscrapers",
    ])
    return (
        f"A dramatic symbol of financial scale for '{subject}': {sym}, lit with "
        f"{accent} light. Convey magnitude and direction only — NO digits, no "
        f"currency symbols. {_FRAMING}")


# --- the selector: 7-step first-match waterfall ----------------------------
def choose_cover(topic: str, headline: str = "", *, person: str | None = None,
                 fmt: str | None = None) -> dict:
    """Pick ONE cover archetype for this topic and build the gpt-image-1 prompt.

    Returns {"archetype": str, "accent": "neon"|"red", "prompt": str}.
    Reuses signals the pipeline already has: `person` from topic_keywords(),
    `fmt` from the format classifier. Pure + free (no API).
    """
    text = f"{topic} {headline}".strip()
    accent = _accent(text)
    subject = (topic or headline or "AI").strip()

    is_danger = bool(_DANGER.search(text))
    # tutorial intent: an explicit how-to verb, but a bare "use" inside a
    # danger headline ("too risky for public USE") must NOT count as a tutorial.
    is_tutorial = (fmt == "tutorial") or (bool(_TOOL.search(text))
                                          and not is_danger)

    # 1. danger override is a COLOR (set above). DREAD topics also win the
    #    archetype early — dread reads best as a cinematic scene, not a CEO
    #    mugshot or a product shot, so it jumps ahead of person/tool checks.
    if is_danger:
        arch, prompt = "staged_dramatic_scene", _staged_scene(subject, accent, text)
    # 2. money / big-number stakes
    elif _MONEY.search(text):
        arch, prompt = "money_number_hero", _money_hero(subject, accent, text)
    # 3. comparison / transformation
    elif _COMPARE.search(text):
        arch, prompt = "before_after_split", _before_after(subject, accent, text)
    # 4. a tool/product how-to (explicit tutorial intent)
    elif is_tutorial and (person or _TOOL.search(text)):
        arch, prompt = "product_screenshot_hero", _product_hero(subject, accent, text)
    # 5. a real person is the ACTOR of the news
    elif person and _ACTOR.search(text):
        arch, prompt = "ceo_face_reaction", _ceo_face(subject, person, accent, text)
    # 6. future / abstract / high-emotion with no concrete subject
    elif _FUTURE.search(text):
        arch, prompt = "staged_dramatic_scene", _staged_scene(subject, accent, text)
    # 7. default fallback — always resolves
    else:
        arch, prompt = "symbolic_object_hero", _symbolic_object(subject, accent, text)

    return {"archetype": arch,
            "accent": "red" if accent == RED else "neon",
            "prompt": prompt}


if __name__ == "__main__":
    tests = [
        ("Anthropic Says New Claude AI Is Too Risky for Public Use", None),
        ("OpenAI raised $40 billion at a $300 billion valuation", "Sam Altman"),
        ("ChatGPT vs Claude: which writes better code", None),
        ("Sam Altman warned AGI is coming sooner than expected", "Sam Altman"),
        ("How to use Claude to build a website in 10 minutes", "Dario Amodei"),
        ("The future of work will never be the same", None),
        ("A new AI model can clone any voice", None),
    ]
    for t, p in tests:
        r = choose_cover(t, person=p)
        print(f"\n[{r['archetype']}  accent={r['accent']}]\n  {t}\n  -> {r['prompt'][:160]}...")
