"""Score and rank Candidate dicts produced by the media_sources modules.

Deterministic. No LLM, no ML. Tweak SOURCE_WEIGHTS to change priorities.
"""

from __future__ import annotations

from typing import Iterable


# Higher score wins. Brand-official sources are top-tier so they beat
# generic YouTube even when the YouTube result has more views.
SOURCE_WEIGHTS: dict[str, int] = {
    # brand official (any brand suffix) -> 100, handled in score()
    "youtube": 70,
    "google_image": 50,
    "pexels_video": 30,
    "pexels_photo": 30,
}


def _is_brand_official(source: str) -> bool:
    return source.endswith("_official")


# --- Demo-vs-talking-head signal -------------------------------------------
# The reel format the user locked (@evolving.ai style) demands the background
# clip SHOW the product DOING something — a screen recording, the UI in use,
# the model generating, the output appearing — NOT a person talking about it.
# A talking-head clip is boring and kills retention. We don't hard-reject
# (user chose "prefer demo, fallback allowed") — we score, so a demo clip wins
# whenever one exists, but a row still ships if only a talking clip is found.

# Title/description words that signal "product in action".
DEMO_WORDS: tuple[str, ...] = (
    "demo", "demonstration", "screen record", "screen recording", "screencast",
    "walkthrough", "walk through", "hands-on", "hands on", "in action",
    "first look", "showcase", "preview", "trailer", "launch", "unveil",
    "introducing", "generating", "generated", "creates", "creating", "builds",
    "building", "how it works", "tutorial", "use case", "ui", "interface",
    "tour", "feature", "capabilities", "test", "testing", "trying", "try",
    "live", "output", "result",
)

# Title/description words that signal a talking head / commentary (penalize).
TALKING_WORDS: tuple[str, ...] = (
    "interview", "podcast", "keynote", "talk", "talks", "explains",
    "explained", "reacts", "reaction", "discussion", "discusses", "opinion",
    "thoughts on", "everything you need to know", "fireside", "panel",
    "conversation", "sit down", "sit-down", "q&a", " q and a", "ama",
    "commentary", "breakdown", "vlog",
)

DEMO_BONUS = 20      # one strong push so a demo clip clears a same-source talker
TALKING_PENALTY = 18  # nearly cancels the bonus, but never zeroes a real source


def _demo_signal(candidate: dict) -> int:
    """Score a video candidate on whether its title reads like a product
    demo (+) vs a talking-head clip (-). Image candidates score 0."""
    if candidate.get("kind") != "video":
        return 0
    extra = candidate.get("extra") or {}
    haystack = " ".join(
        str(candidate.get(k) or "") for k in ("title", "page_url")
    )
    haystack += " " + str(extra.get("channel") or "")
    haystack = haystack.lower()

    delta = 0
    if any(w in haystack for w in DEMO_WORDS):
        delta += DEMO_BONUS
    if any(w in haystack for w in TALKING_WORDS):
        delta -= TALKING_PENALTY
    return delta


def score(candidate: dict, *, matched_brands: list[str] | None = None) -> int:
    """Return a numeric score for a single candidate (higher is better).

    Includes boosts/penalties:
      - YouTube channel matches a brand mentioned in topic -> +10
      - view_count > 100k -> +5
      - video duration > 600s -> -10
      - google_image hosted on a matched brand's domain -> +5
      - reel-ideal duration (3-60s) -> +5
    """
    src = candidate["source"]
    base = 100 if _is_brand_official(src) else SOURCE_WEIGHTS.get(src, 10)

    extra = candidate.get("extra") or {}

    # YouTube boosts
    if src == "youtube":
        channel = (extra.get("channel") or "").lower()
        if matched_brands and any(b in channel for b in matched_brands):
            base += 10
        views = extra.get("view_count") or 0
        if views and views >= 100_000:
            base += 5

    # Video duration logic (applies to all video kinds)
    if candidate.get("kind") == "video":
        dur = candidate.get("duration_s") or 0
        if dur > 600:
            base -= 10
        elif 3 <= dur <= 60:
            base += 5
        # Prefer product-in-action footage over talking-head clips.
        base += _demo_signal(candidate)

    # google_image hosted on a brand domain
    if src == "google_image" and matched_brands:
        host = (extra.get("host") or "").lower()
        page_url = (candidate.get("page_url") or "").lower()
        for brand in matched_brands:
            if brand in host or brand in page_url:
                base += 5
                break

    return base


def rank(
    candidates: Iterable[dict],
    *,
    matched_brands: list[str] | None = None,
) -> list[dict]:
    """Return candidates sorted by score (desc), then by image area
    (desc) as a tiebreak.
    """
    seen_urls: set[str] = set()
    deduped: list[dict] = []
    for c in candidates:
        url = c.get("media_url") or c.get("page_url")
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        deduped.append(c)

    def sort_key(c: dict) -> tuple:
        s = score(c, matched_brands=matched_brands)
        area = (c.get("width") or 0) * (c.get("height") or 0)
        # Negate so higher = earlier under ascending sort
        return (-s, -area)

    return sorted(deduped, key=sort_key)


def pick_best(
    candidates: Iterable[dict],
    *,
    matched_brands: list[str] | None = None,
) -> tuple[dict | None, list[dict]]:
    """Return (winner, backups) where backups are the next-best entries
    (max 4). Returns (None, []) when candidates is empty.
    """
    ranked = rank(candidates, matched_brands=matched_brands)
    if not ranked:
        return None, []
    return ranked[0], ranked[1:5]
