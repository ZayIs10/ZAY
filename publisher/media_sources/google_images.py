"""Web image search via DuckDuckGo (which proxies Google/Bing image
results). Uses the `duckduckgo_search` package that's already in
requirements.txt.

Returns Candidate dicts with kind="image".
"""

from __future__ import annotations

import logging

log = logging.getLogger("media_sources.google_images")


def _import_ddgs():
    try:
        from duckduckgo_search import DDGS  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "duckduckgo-search is not installed; expected in requirements.txt"
        ) from exc
    return DDGS


def search_images(
    query: str,
    limit: int = 5,
    *,
    site_filter: str | None = None,
) -> list[dict]:
    """Return up to `limit` image Candidate dicts for `query`.

    `site_filter` (e.g. "openai.com") restricts results to that host
    via DDG's `site:` operator.
    """
    DDGS = _import_ddgs()
    q = query
    if site_filter:
        q = f"{q} site:{site_filter}"

    try:
        with DDGS() as ddgs:
            raw = list(ddgs.images(q, max_results=limit))
    except Exception as exc:
        log.warning("DDG image search failed for %r: %s", q, exc)
        return []

    candidates: list[dict] = []
    for entry in raw:
        image_url = entry.get("image") or entry.get("url")
        if not image_url:
            continue
        candidates.append({
            "source": "google_image",
            "kind": "image",
            "title": entry.get("title") or image_url,
            "page_url": entry.get("url") or image_url,
            "media_url": image_url,
            "thumbnail": entry.get("thumbnail") or image_url,
            "duration_s": 0.0,
            "width": int(entry.get("width") or 0),
            "height": int(entry.get("height") or 0),
            "extra": {
                "source_site": entry.get("source"),
                "host": entry.get("source"),
            },
        })
    return candidates


def search_videos(query: str, limit: int = 5) -> list[dict]:
    """Not implemented — image-only source."""
    return []
