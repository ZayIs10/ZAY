"""Source-company logo fetcher — the @evolving.ai move where every slide carries
the logo of the company the story is about (the orange Claude starburst on their
Mythos cover, the OpenAI mark on an OpenAI story, etc.).

FREE + auto: given a company name pulled from the topic (Anthropic, OpenAI,
Google...), resolve it to a domain and download that brand's logo ONCE from a
free logo CDN, caching it to assets/logos/<company>.png. After the first fetch
it's a local file — no network, no cost.

Used by carousel_format.cover_slide / content_slide. Degrades gracefully: if the
company is unknown or the network fails, returns None and the slide just renders
without a source logo (no crash).
"""
from __future__ import annotations

import os
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGO_DIR = os.path.join(ROOT, "assets", "logos")

# Company keyword -> primary domain. Free logo CDNs (logo.dev, Clearbit) key on
# the domain, not the name. Keys MUST stay in sync with BRAND_PEOPLE in
# carousel_image_pipeline.py so any topic that resolves a brand also gets a logo.
# IMPORTANT: when the story is about a PRODUCT (Claude, ChatGPT, Gemini...), the
# slide should carry that PRODUCT'S logo, not its parent company's. @evolving.ai
# does exactly this — their OpenAI/ChatGPT story shows the ChatGPT mark, not a
# generic "company" logo. So product keys point at the product's own domain
# (claude.ai, gemini.google.com) whose favicon IS the product logo, while the
# company keys (anthropic, google) keep the corporate mark. The product key is
# matched FIRST (see PRODUCT_FIRST below) so "Anthropic's Claude" → Claude logo.
BRAND_DOMAINS = {
    # --- products (their own logo) ---
    "claude": "claude.ai",
    "chatgpt": "chatgpt.com",
    "gpt": "chatgpt.com",
    "gemini": "gemini.google.com",
    "copilot": "copilot.microsoft.com",
    "grok": "grok.com",
    "llama": "llama.com",
    "midjourney": "midjourney.com",
    "perplexity": "perplexity.ai",
    "mistral": "mistral.ai",
    # --- companies (corporate mark, used only when no product is named) ---
    "anthropic": "anthropic.com",
    "openai": "openai.com",
    "google": "google.com",
    "deepmind": "deepmind.google",
    "meta": "meta.com",
    "xai": "x.ai",
    "tesla": "tesla.com",
    "nvidia": "nvidia.com",
    "microsoft": "microsoft.com",
}

# Products take priority over their parent company when BOTH appear in the text
# (e.g. "Anthropic says new Claude is risky" → show the Claude logo). _canon
# scans this list first, in order, before falling back to a plain substring scan.
PRODUCT_FIRST = ["claude", "chatgpt", "gemini", "copilot", "grok", "llama",
                 "midjourney", "perplexity", "mistral"]

# Free, no-token logo sources (probed 2026-06-13). Clearbit's free logo API is
# dead (DNS gone) and logo.dev now 401s without a token. Google's favicon
# service returns a clean 256x256 brand PNG with no auth — that's the primary.
# DuckDuckGo's icon service is the fallback (smaller, 32px, but reliable).
_SOURCES = [
    "https://www.google.com/s2/favicons?domain={domain}&sz=256",
    "https://icons.duckduckgo.com/ip3/{domain}.ico",
    "https://unavatar.io/{domain}",
]

_HEADERS = {"User-Agent": "Mozilla/5.0 (GenZCapital carousel logo fetcher)"}


def _canon(name: str) -> str | None:
    """Map a free-text company/brand name to a cache key in BRAND_DOMAINS."""
    if not name:
        return None
    low = name.strip().lower()
    if low in BRAND_DOMAINS:
        return low
    # PRODUCT wins over parent company: scan products first so a topic naming
    # both ("Anthropic's Claude") resolves to the product logo (Claude), not the
    # corporate one (Anthropic).
    for key in PRODUCT_FIRST:
        if key in low:
            return key
    # then any remaining brand by substring
    for key in BRAND_DOMAINS:
        if key in low:
            return key
    return None


def logo_path(name: str) -> str | None:
    """Return a local PNG path for this company's logo, fetching+caching on the
    first call. Returns None if the company is unknown or the fetch fails."""
    key = _canon(name)
    if not key:
        return None
    os.makedirs(LOGO_DIR, exist_ok=True)
    dest = os.path.join(LOGO_DIR, f"{key}.png")
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return dest

    domain = BRAND_DOMAINS[key]
    for tmpl in _SOURCES:
        url = tmpl.format(domain=domain)
        try:
            req = urllib.request.Request(url, headers=_HEADERS)
            with urllib.request.urlopen(req, timeout=15) as r:
                ctype = r.headers.get("Content-Type", "")
                data = r.read()
            # accept only real images of plausible size (skip tiny error blobs)
            if data and len(data) > 200 and "image" in ctype:
                with open(dest, "wb") as f:
                    f.write(data)
                return dest
        except Exception:
            continue
    return None


def logo_for_brands(brands: list[str] | None, topic: str = "") -> str | None:
    """Best logo for a slide: PRODUCT logo first (Claude/ChatGPT/...), then the
    detected company brands, then the topic text. Returns a cached path or None.

    Products win even when the upstream brand list only carries the parent
    company (topic_keywords returns ['anthropic'] for a Claude story) — we
    re-scan the full topic+brands text for a product name so the slide shows
    the Claude starburst, not the Anthropic 'A'. This is the @evolving.ai move:
    the story's PRODUCT logo, not its corporate logo."""
    blob = f"{topic} {' '.join(brands or [])}".strip()
    for prod in PRODUCT_FIRST:
        if prod in blob.lower():
            p = logo_path(prod)
            if p:
                return p
    for b in (brands or []):
        p = logo_path(b)
        if p:
            return p
    return logo_path(topic)


if __name__ == "__main__":
    import sys
    name = " ".join(sys.argv[1:]) or "anthropic"
    p = logo_path(name)
    print(f"{name!r} -> {p}")
