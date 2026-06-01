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
