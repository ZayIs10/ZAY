"""Score and rank Candidate dicts produced by the media_sources modules.

Deterministic. No LLM, no ML. Tweak SOURCE_WEIGHTS to change priorities.
"""

from __future__ import annotations

import re
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


# --- Topic relevance signal ------------------------------------------------
# THE bug this fixes: search for "Claude Opus 4.8" but a clip titled "Claude
# builds 3D worlds" wins because it's a demo with views. The query used the
# keyword, but the WINNER was picked without ever checking the clip's title
# against the topic. A clip must literally be about the thing the topic names
# (the user's locked rule: visuals must match the voiceover). So we compare
# the candidate title to the topic's keywords and reward overlap / punish a
# total mismatch — heavily weighting DISTINCTIVE tokens (version numbers like
# "4.8", product names) over common English words.

# Words too generic to prove a clip is on-topic. Matching only these is NOT
# relevance (e.g. "AI", "Claude" appear in every clip in this niche).
_RELEVANCE_STOPWORDS: frozenset[str] = frozenset({
    "the", "a", "an", "and", "or", "of", "to", "for", "in", "on", "with",
    "is", "are", "now", "your", "you", "how", "what", "new", "just", "this",
    "that", "it", "its", "by", "at", "as", "be", "can", "will", "from",
    "ai", "claude", "anthropic",  # niche-universal -> not distinctive
})

# A version-like token: 4.8, v2, gpt-5, 4o, 3.5 ... these are the strongest
# possible on-topic signal and a clip missing the right one is the classic
# wrong-clip bug ("Opus 4.8" footage vs "Opus 4.5" footage).
_VERSION_RE = re.compile(r"^v?\d+(\.\d+)*[a-z]?$|^\d+[a-z]$", re.IGNORECASE)

RELEVANCE_TERM_BONUS = 14   # per distinctive topic word found in the title
RELEVANCE_VERSION_BONUS = 30  # a matching version number is decisive
RELEVANCE_MISS_PENALTY = 60   # title shares NO distinctive topic word -> sink it
RELEVANCE_MAX_BONUS = 50      # cap the cumulative term bonus


def topic_keywords(topic: str) -> tuple[set[str], set[str]]:
    """Split a topic into (distinctive_words, version_tokens).

    distinctive_words: lowercased content words minus niche-universal stopwords.
    version_tokens: tokens that look like a version/model number (4.8, gpt-5).
    """
    words: set[str] = set()
    versions: set[str] = set()
    for raw in re.findall(r"[A-Za-z0-9.\-]+", topic or ""):
        bare = raw.strip(".-").lower()
        if not bare:
            continue
        if _VERSION_RE.match(bare):
            versions.add(bare)
            continue
        if bare in _RELEVANCE_STOPWORDS or len(bare) < 3:
            continue
        words.add(bare)
    return words, versions


def _relevance_signal(candidate: dict, topic: str, context: str = "") -> int:
    """Score how well a candidate's title matches the topic keywords.

    `context` (e.g. the row's Key Points) widens the legitimate-match
    vocabulary so a clip phrased differently from the title — "Claude
    QuickBooks demo" for topic "Claude Runs Your Small Business" — isn't
    wrongly sunk. Version numbers still come only from the TOPIC (the
    decisive signal); context only adds extra content words.

    Returns a positive boost for on-topic clips and a hard negative for a
    title that shares no distinctive topic word at all (the off-topic clip
    that should never win). Returns 0 when there's no topic to compare or no
    title (can't judge -> don't penalize)."""
    title = str(candidate.get("title") or "")
    if not topic or not title:
        return 0
    words, versions = topic_keywords(topic)
    if context:
        ctx_words, ctx_versions = topic_keywords(context)
        words = words | ctx_words
        versions = versions | ctx_versions
    if not words and not versions:
        return 0

    title_l = title.lower()
    title_tokens = {
        t.strip(".-").lower()
        for t in re.findall(r"[A-Za-z0-9.\-]+", title_l)
    }

    delta = 0
    matched_terms = sum(1 for w in words if w in title_l)
    delta += min(matched_terms * RELEVANCE_TERM_BONUS, RELEVANCE_MAX_BONUS)

    # Version numbers are decisive: the right one is a strong boost.
    if versions:
        if versions & title_tokens:
            delta += RELEVANCE_VERSION_BONUS

    # A title that shares NO distinctive topic word is off-topic — sink it.
    if matched_terms == 0 and not (versions & title_tokens):
        delta -= RELEVANCE_MISS_PENALTY

    return delta


def score(
    candidate: dict,
    *,
    matched_brands: list[str] | None = None,
    topic: str | None = None,
    context: str = "",
) -> int:
    """Return a numeric score for a single candidate (higher is better).

    Includes boosts/penalties:
      - title matches topic keywords (esp. version numbers) -> big boost
      - title shares NO distinctive topic word -> big penalty (off-topic)
      - YouTube channel matches a brand mentioned in topic -> +10
      - view_count > 100k -> +5
      - video duration > 600s -> -10
      - google_image hosted on a matched brand's domain -> +5
      - reel-ideal duration (3-60s) -> +5
    """
    src = candidate["source"]
    base = 100 if _is_brand_official(src) else SOURCE_WEIGHTS.get(src, 10)

    extra = candidate.get("extra") or {}

    # Topic relevance — applies to BOTH video and image candidates so an
    # off-topic still doesn't win either. This is the primary fix for the
    # "searched X, got a clip about Y" bug.
    if topic:
        base += _relevance_signal(candidate, topic, context)

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
    topic: str | None = None,
    context: str = "",
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
        s = score(c, matched_brands=matched_brands, topic=topic, context=context)
        area = (c.get("width") or 0) * (c.get("height") or 0)
        # Negate so higher = earlier under ascending sort
        return (-s, -area)

    return sorted(deduped, key=sort_key)


def pick_best(
    candidates: Iterable[dict],
    *,
    matched_brands: list[str] | None = None,
    topic: str | None = None,
    context: str = "",
) -> tuple[dict | None, list[dict]]:
    """Return (winner, backups) where backups are the next-best entries
    (max 4). Returns (None, []) when candidates is empty.
    """
    ranked = rank(candidates, matched_brands=matched_brands,
                  topic=topic, context=context)
    if not ranked:
        return None, []
    return ranked[0], ranked[1:5]
