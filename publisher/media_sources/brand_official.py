"""Brand-official media discovery.

For brands matched by brand_detect.detect_brands():
  - search_videos(brand, query, limit) -> hit the brand's YouTube channel
  - search_images(brand, query, limit) -> scrape og:image from the brand's
    blog/news posts that mention the query

Falls through gracefully when the brand has no channel/blog configured
or when the network requests fail.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urljoin, urlparse

import requests

from publisher.media_sources import youtube as ytsrc
from publisher.media_sources.brand_detect import brand_config

log = logging.getLogger("media_sources.brand_official")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
}

_OG_IMAGE_RE = re.compile(
    r'<meta\s+[^>]*property=["\']og:image["\']\s+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_OG_TITLE_RE = re.compile(
    r'<meta\s+[^>]*property=["\']og:title["\']\s+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)


def search_videos(brand: str, query: str, limit: int = 5) -> list[dict]:
    """Run a YouTube search constrained to the brand's official channel
    by appending the channel handle to the query. Reuses youtube.py.
    """
    cfg = brand_config(brand)
    handle = cfg.get("youtube_channel")
    channel_id = cfg.get("youtube_handle_id")
    if not handle and not channel_id:
        return []

    cands = ytsrc.search_videos(
        query, limit=limit,
        channel_handle=handle, channel_id=channel_id,
    )
    # Re-label so the scoring module knows these came from the official
    # channel and gives them the higher tier.
    for c in cands:
        c["source"] = f"{brand}_official"
        c["extra"]["brand"] = brand
        c["extra"]["is_official"] = True
    return cands


def _fetch(url: str, timeout: float = 15.0) -> str | None:
    try:
        r = requests.get(url, headers=_HEADERS, timeout=timeout)
        r.raise_for_status()
        return r.text
    except Exception as exc:
        log.debug("brand_official fetch failed %s: %s", url, exc)
        return None


def _collect_post_links(html: str, base_url: str, brand: str) -> list[str]:
    """Pull out post URLs from a blog index page.

    We accept any same-host link that looks like a content page. The
    filter is loose on purpose — the per-brand site shapes vary and a
    perfect parser isn't worth the complexity.
    """
    base_host = urlparse(base_url).netloc
    links: list[str] = []
    seen: set[str] = set()
    for m in _HREF_RE.finditer(html):
        href = m.group(1)
        if href.startswith("#") or href.startswith("mailto:"):
            continue
        full = urljoin(base_url, href)
        host = urlparse(full).netloc
        if not host or host != base_host:
            continue
        path = urlparse(full).path
        # Skip obvious non-content paths
        if not path or path in ("/", "/news", "/news/", "/blog", "/blog/"):
            continue
        if any(seg in path for seg in (
                "/research", "/news/", "/blog/", "/posts/",
                "/index", "/article")):
            if full not in seen:
                seen.add(full)
                links.append(full)
    return links


def search_images(brand: str, query: str, limit: int = 5) -> list[dict]:
    """Scrape the brand's blog index for recent posts, then pull og:image
    from each post page. Filters posts whose og:title contains any query
    token.
    """
    cfg = brand_config(brand)
    blog = cfg.get("blog_index")
    if not blog:
        return []

    index_html = _fetch(blog)
    if not index_html:
        return []

    post_links = _collect_post_links(index_html, blog, brand)
    if not post_links:
        return []

    query_tokens = {tok.lower() for tok in re.split(r"\W+", query) if len(tok) > 2}
    candidates: list[dict] = []
    # Limit how many post pages we fetch to keep this fast.
    for post_url in post_links[: max(limit * 3, 10)]:
        if len(candidates) >= limit:
            break
        page_html = _fetch(post_url)
        if not page_html:
            continue
        title_m = _OG_TITLE_RE.search(page_html)
        title = title_m.group(1) if title_m else post_url
        # If we have query tokens, require at least one to appear in title
        if query_tokens and not any(t in title.lower() for t in query_tokens):
            continue
        og_m = _OG_IMAGE_RE.search(page_html)
        if not og_m:
            continue
        image_url = og_m.group(1)
        candidates.append({
            "source": f"{brand}_official",
            "kind": "image",
            "title": title,
            "page_url": post_url,
            "media_url": image_url,
            "thumbnail": image_url,
            "duration_s": 0.0,
            "width": 0,
            "height": 0,
            "extra": {"brand": brand, "is_official": True},
        })
    return candidates
