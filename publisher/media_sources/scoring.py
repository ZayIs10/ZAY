"""Score and rank Candidate dicts produced by the media_sources modules.

Deterministic. No LLM, no ML. Tweak SOURCE_WEIGHTS to change priorities.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
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

# Concept (story-type) matching. For abstract topics whose literal words never
# appear in any clip title ("Worth Almost $1 Trillion"), we match the STORY
# TYPE instead: a valuation story should pull funding/valuation footage, not a
# product demo. A title carrying the concept's terms is on-concept.
CONCEPT_TITLE_BONUS = 22   # per concept term in the title (caps below)
CONCEPT_MAX_BONUS = 44     # cap cumulative concept bonus


def _concept_title_terms(topic: str, context: str = "") -> set[str]:
    """Concept (story-type) title terms for `topic` (+context). Imported
    lazily and defensively so a missing/erroring concept module degrades to
    plain keyword behavior instead of breaking media-finding."""
    try:
        from publisher.media_sources.topic_concept import concept_title_terms
    except Exception:  # noqa: BLE001
        try:
            from topic_concept import concept_title_terms  # flat import
        except Exception:  # noqa: BLE001
            return set()
    try:
        return concept_title_terms(topic, context)
    except Exception:  # noqa: BLE001
        return set()


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

    # Concept (story-type) match: for abstract topics whose literal words never
    # appear in a clip title ("Worth Almost $1 Trillion"), reward a title that
    # carries the story's concept terms (funding/valuation/etc.). This is how a
    # valuation clip wins over a product demo. Concept terms come from the
    # TOPIC + context via topic_concept.concept_title_terms().
    concept_terms = _concept_title_terms(topic, context)
    on_concept = False
    if concept_terms:
        concept_hits = sum(1 for t in concept_terms if t in title_l)
        if concept_hits:
            on_concept = True
            delta += min(concept_hits * CONCEPT_TITLE_BONUS, CONCEPT_MAX_BONUS)

    # A title that shares NO distinctive topic word AND is not on-concept is
    # off-topic — sink it. (An on-concept clip is spared the penalty even if it
    # lacks the literal topic words, e.g. "$183 billion" footage for a
    # "$1 trillion" story.)
    if matched_terms == 0 and not (versions & title_tokens) and not on_concept:
        delta -= RELEVANCE_MISS_PENALTY

    return delta


# --- Rival-company signal (HARD BLOCK) -------------------------------------
# THE bug the user hit repeatedly: a topic about ANTHROPIC ("Anthropic just
# released its most dangerous model") pulled an OPENAI clip showing GPT-4.6,
# because the OpenAI video's title happened to contain generic words ("danger-
# ous", "model") and the relevance check only looks at the TITLE, never the
# channel/company. The fix: if the topic names a specific brand and a clip
# comes from a DIFFERENT known brand's channel, it is the WRONG COMPANY — block
# it outright. (User chose "hard-block rivals" over a soft penalty: never show
# a competitor's footage for a brand story; fall back to neutral Pexels instead.)
#
# We identify a clip's company from its channel name/id using the same BRANDS
# table that detects the topic's brand, so the two are always consistent.
RIVAL_BLOCK_PENALTY = 100_000  # effectively un-winnable; only used as last resort


def _brand_of_channel(candidate: dict) -> str | None:
    """Best-effort: which known brand owns this clip's channel? Returns the
    brand key (e.g. 'openai') or None if the channel isn't a recognized brand.

    Matches on channel_id (exact, most reliable) first, then on the channel
    name containing a brand alias. Defensive: a missing BRANDS table or any
    error degrades to None (no rival blocking) rather than breaking scoring.
    """
    extra = candidate.get("extra") or {}
    channel_name = str(extra.get("channel") or "").lower()
    channel_id = str(extra.get("channel_id") or "").strip()
    if not channel_name and not channel_id:
        return None
    try:
        from publisher.media_sources.brand_detect import BRANDS
    except Exception:  # noqa: BLE001
        try:
            from brand_detect import BRANDS  # flat import
        except Exception:  # noqa: BLE001
            return None

    for brand, cfg in BRANDS.items():
        # 1. Exact official channel id match — unambiguous.
        if channel_id and channel_id == (cfg.get("youtube_handle_id") or ""):
            return brand
        # 2. The official @handle (minus the @) appearing in the channel name.
        handle = str(cfg.get("youtube_channel") or "").lstrip("@").lower()
        if handle and handle in channel_name.replace(" ", ""):
            return brand
        # 3. A brand alias appearing as a word in the channel name
        #    ("OpenAI", "Anthropic", "Google DeepMind").
        for alias in cfg.get("aliases", []):
            a = alias.lower()
            if len(a) >= 4 and a in channel_name:
                return brand
    return None


def _rival_brand_signal(
    candidate: dict, matched_brands: list[str] | None
) -> int:
    """Return a massive negative score when this clip's channel belongs to a
    KNOWN brand that is NOT the topic's brand — i.e. the wrong company. Returns
    0 when the topic names no brand, the clip's channel isn't a recognized
    brand, or the channel matches the topic's brand (correct company).

    Only YouTube clips carry a company identity; Pexels/stock have none, so
    they're never blocked (they're the intended neutral fallback)."""
    if not matched_brands:
        return 0
    if candidate.get("source") != "youtube" and not str(
        candidate.get("source") or ""
    ).endswith("_official"):
        return 0
    clip_brand = _brand_of_channel(candidate)
    if not clip_brand:
        return 0  # unknown channel — can't prove it's a rival; don't block
    topic_brands = {b.lower() for b in matched_brands}
    if clip_brand.lower() in topic_brands:
        return 0  # same company — correct
    return -RIVAL_BLOCK_PENALTY  # different known company — WRONG, block it


# --- Recency signal --------------------------------------------------------
# THE other half of the user's bug: "this model has been pushed for months" —
# a topic about a brand-NEW release ("just released") pulled a months-old clip
# because nothing rewarded fresh uploads. News footage must be current. We
# reward recent uploads strongly and lightly penalize stale ones, so the newest
# on-topic clip wins. Candidates with no known upload date are left neutral
# (we can't punish what we can't date).
RECENCY_BONUS_FRESH = 45    # uploaded within FRESH_DAYS — strong push
RECENCY_BONUS_RECENT = 20   # within RECENT_DAYS — moderate
RECENCY_PENALTY_STALE = 35  # older than STALE_DAYS — sink old "news" footage
FRESH_DAYS = 30
RECENT_DAYS = 120
STALE_DAYS = 365


def _parse_upload_dt(extra: dict) -> datetime | None:
    """Pull an upload datetime (UTC) from a candidate's extra, trying the
    several shapes the sources produce: ISO publishedAt (API), YYYYMMDD
    upload_date (yt-dlp), or a unix timestamp. Returns None if undatable."""
    # 1. YouTube Data API: ISO-8601 string, e.g. "2026-06-20T13:00:00Z".
    iso = str(extra.get("published_at") or "").strip()
    if iso:
        try:
            return datetime.fromisoformat(
                iso.replace("Z", "+00:00")
            ).astimezone(timezone.utc)
        except ValueError:
            pass
    # 2. yt-dlp upload_date: "YYYYMMDD".
    ud = str(extra.get("upload_date") or "").strip()
    if len(ud) == 8 and ud.isdigit():
        try:
            return datetime(
                int(ud[:4]), int(ud[4:6]), int(ud[6:8]), tzinfo=timezone.utc
            )
        except ValueError:
            pass
    # 3. unix timestamp.
    ts = extra.get("timestamp")
    if ts:
        try:
            return datetime.fromtimestamp(float(ts), tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            pass
    return None


def _recency_signal(candidate: dict, *, now: datetime | None = None) -> int:
    """Boost recent video uploads, penalize stale ones. 0 for images or
    undatable candidates. `now` is injectable for testing."""
    if candidate.get("kind") != "video":
        return 0
    dt = _parse_upload_dt(candidate.get("extra") or {})
    if dt is None:
        return 0
    now = now or datetime.now(timezone.utc)
    age_days = (now - dt).days
    if age_days < 0:           # clock skew / scheduled future date — treat fresh
        return RECENCY_BONUS_FRESH
    if age_days <= FRESH_DAYS:
        return RECENCY_BONUS_FRESH
    if age_days <= RECENT_DAYS:
        return RECENCY_BONUS_RECENT
    if age_days > STALE_DAYS:
        return -RECENCY_PENALTY_STALE
    return 0


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

    # WRONG-COMPANY HARD BLOCK — first, because it overrides everything. If the
    # topic is about brand X and this clip's channel is a DIFFERENT known brand
    # (an OpenAI video for an Anthropic topic), sink it so far it can only win
    # if literally nothing else exists. The single biggest fix for the user's
    # repeated complaint (wrong company in the video).
    base += _rival_brand_signal(candidate, matched_brands)

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
        # Strongly prefer recent uploads so a "just released" topic never pulls
        # months-old footage (the user's GPT-4.6-for-a-new-model complaint).
        base += _recency_signal(candidate)

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
