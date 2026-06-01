"""Detect which AI brand(s) a topic mentions, so the finder can hit the
brand's official site / YouTube channel before falling back to general
sources.

Used by publisher/media_finder.py. Keep BRANDS as a plain dict — easy to
edit when a new model launches. Matching is case-insensitive substring
against `Topic + " " + Key Points`.
"""

from __future__ import annotations


BRANDS: dict[str, dict] = {
    "openai": {
        "aliases": [
            "openai", "open ai", "chatgpt", "gpt-4", "gpt4", "gpt-5",
            "sora", "dall-e", "dalle", "whisper", "sam altman",
        ],
        "site": "https://openai.com",
        "blog_index": "https://openai.com/news/",
        "youtube_channel": "@OpenAI",
        "youtube_handle_id": "UCXZCJLdBC09xxGZ6gcdrc6A",
    },
    "anthropic": {
        "aliases": [
            "anthropic", "claude", "claude 3", "claude 4", "dario amodei",
        ],
        "site": "https://anthropic.com",
        "blog_index": "https://www.anthropic.com/news",
        "youtube_channel": "@anthropic-ai",
        "youtube_handle_id": None,
    },
    "google": {
        "aliases": [
            "google ai", "gemini", "deepmind", "veo", "imagen",
            "notebooklm", "google bard", "bard",
        ],
        "site": "https://blog.google",
        "blog_index": "https://blog.google/technology/ai/",
        "youtube_channel": "@Google",
        "youtube_handle_id": None,
    },
    "meta": {
        "aliases": [
            "meta ai", "llama", "llama 3", "llama 4", "yann lecun",
        ],
        "site": "https://ai.meta.com",
        "blog_index": "https://ai.meta.com/blog/",
        "youtube_channel": "@MetaAI",
        "youtube_handle_id": None,
    },
    "mistral": {
        "aliases": ["mistral", "mixtral", "mistral ai"],
        "site": "https://mistral.ai",
        "blog_index": "https://mistral.ai/news/",
        "youtube_channel": None,
        "youtube_handle_id": None,
    },
    "xai": {
        "aliases": ["xai", "x.ai", "grok"],
        "site": "https://x.ai",
        "blog_index": "https://x.ai/news",
        "youtube_channel": None,
        "youtube_handle_id": None,
    },
    "microsoft": {
        "aliases": ["copilot", "microsoft ai", "github copilot"],
        "site": "https://www.microsoft.com/en-us/ai",
        "blog_index": "https://blogs.microsoft.com/ai/",
        "youtube_channel": "@Microsoft",
        "youtube_handle_id": None,
    },
}


def detect_brands(text: str) -> list[str]:
    """Return the brand keys whose aliases appear in `text`.

    Order matches first-appearance position in `text`, so the most
    prominent brand comes first.
    """
    if not text:
        return []
    haystack = text.lower()
    hits: list[tuple[int, str]] = []
    seen: set[str] = set()
    for brand, cfg in BRANDS.items():
        if brand in seen:
            continue
        for alias in cfg["aliases"]:
            idx = haystack.find(alias.lower())
            if idx >= 0:
                hits.append((idx, brand))
                seen.add(brand)
                break
    hits.sort(key=lambda h: h[0])
    return [b for _, b in hits]


def brand_config(brand: str) -> dict:
    """Return the BRANDS entry for `brand` (raises KeyError if unknown)."""
    return BRANDS[brand]
