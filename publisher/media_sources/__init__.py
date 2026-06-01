"""Media discovery sources for publisher/media_finder.py.

Each module exposes:
    search_videos(query: str, limit: int = 5) -> list[Candidate]
    search_images(query: str, limit: int = 5) -> list[Candidate]

Some modules implement only one of the two (e.g. google_images.py is
images-only, pexels.py implements both).

`Candidate` is a plain dict with keys:
    source       : str   — short source ID ("youtube", "pexels_video", ...)
    kind         : str   — "video" | "image"
    title        : str   — display title (falls back to URL host)
    page_url     : str   — human page URL (YouTube watch URL, blog post)
    media_url    : str   — direct media URL (mp4, jpg). May equal page_url.
    thumbnail    : str   — preview image URL (may be empty)
    duration_s   : float — video duration in seconds, 0 for images
    width        : int   — pixel width, 0 if unknown
    height       : int   — pixel height, 0 if unknown
    extra        : dict  — source-specific raw data (view count, etc.)
"""
