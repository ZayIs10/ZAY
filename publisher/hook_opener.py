"""Viral hook opener — fetch a scroll-stopping opener clip from viralhooks.org.

https://viralhooks.org hosts a free library of short (~3-8s) 1080x1920 "hook"
videos (bike crash, watermelon split, ...) made to be spliced in as the FIRST
seconds of a reel so viewers stop scrolling. Per the user's request
(2026-07-19), every tweet-card reel now opens with ONE whole hook video from
the site, played full-screen with the tweet card overlaid, before the normal
reel body (see compositor.build's hook_video parameter).

Contract (deliberately soft):
    fetch_hook_for_topic(topic, dest_dir) -> Path | None

NEVER raises: any failure (site down, bad file, feature disabled) returns
None and the reel builds exactly as before — the hook must never kill a
render. Every failure logs at WARNING with the prefix "viral hook" so it's
easy to grep in the Actions log (a silent best-effort is how the transcript
module went missing for weeks).

Selection is DETERMINISTIC per topic: the catalog is shuffled with the Topic
string as the RNG seed, so a re-run of the same row picks the same hook
(idempotent builds) while different topics rotate through the library.

Env overrides:
    DISABLE_VIRAL_HOOK=1   skip hooks entirely (kill-switch, no code change)
    VIRAL_HOOK_SLUG=name   force one specific hook (testing / taste)

The download is a plain HTTPS GET. viralhooks.org does NOT bot-block
datacenter IPs, so it needs no proxy — never route it through PROXY_URL
(that would spend paid residential GB for nothing).
"""

from __future__ import annotations

import logging
import os
import random
import re
import sys
from pathlib import Path

import requests

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

log = logging.getLogger("hook_opener")

BASE_URL = "https://viralhooks.org"
_TIMEOUT = 20  # seconds per HTTP call — a slow site must not stall the build

# Snapshot of the library on 2026-07-19 — the fallback when the live scrape
# fails. The scrape below picks up hooks the site adds later.
FALLBACK_SLUGS = [
    "bike-crash",
    "flip-on-head",
    "front-roll",
    "kick-golf-ball",
    "lil-whip",
    "lil-zipline",
    "powder-boom",
    "sack-whack",
    "slip-run",
    "trail-flipper",
    "tuck-roll",
    "watermelon-split",
]

# A hook outside these bounds is rejected: <1s is a broken file, >15s would
# eat most of the 60s reel budget (the known hooks run ~3-8s).
MIN_SECONDS = 1.0
MAX_SECONDS = 15.0
_MIN_BYTES = 100_000  # anything smaller is an error page, not a video

_catalog_cache: list[str] | None = None


def _catalog() -> list[str]:
    """Hook slugs available on viralhooks.org, sorted for stable seeding.

    Scraped live from the homepage's /hooks/<slug>.mp4 links so newly added
    hooks join the rotation automatically; falls back to the 2026-07-19
    snapshot when the site is unreachable or the markup changed.
    """
    global _catalog_cache
    if _catalog_cache is not None:
        return _catalog_cache
    slugs: list[str] = []
    try:
        resp = requests.get(BASE_URL + "/", timeout=_TIMEOUT)
        resp.raise_for_status()
        slugs = sorted(set(re.findall(r"/hooks/([a-z0-9-]+)\.mp4", resp.text)))
    except Exception as exc:  # noqa: BLE001 — scrape is best-effort
        log.warning("viral hook: catalog scrape failed (%s) — using the "
                    "built-in fallback list.", exc)
    if not slugs:
        slugs = list(FALLBACK_SLUGS)
    _catalog_cache = slugs
    return slugs


def _download(slug: str, dest_dir: Path) -> Path | None:
    """Download one hook MP4; return the path, or None if it's unusable."""
    url = f"{BASE_URL}/hooks/{slug}.mp4"
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / f"hook_{slug}.mp4"
    try:
        with requests.get(url, timeout=_TIMEOUT, stream=True) as resp:
            resp.raise_for_status()
            with open(out, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=1 << 16):
                    fh.write(chunk)
    except Exception as exc:  # noqa: BLE001 — caller tries the next slug
        log.warning("viral hook: download failed for %r (%s)", slug, exc)
        return None

    if not out.exists() or out.stat().st_size < _MIN_BYTES:
        log.warning("viral hook: %r came back too small (%d bytes) — "
                    "likely an error page, skipping.",
                    slug, out.stat().st_size if out.exists() else 0)
        return None

    try:
        from publisher.compositor import probe_duration
        dur = probe_duration(out)
    except Exception as exc:  # noqa: BLE001
        log.warning("viral hook: could not probe %r (%s) — skipping.", slug, exc)
        return None
    if not (MIN_SECONDS <= dur <= MAX_SECONDS):
        log.warning("viral hook: %r is %.1fs (allowed %.0f-%.0fs) — skipping.",
                    slug, dur, MIN_SECONDS, MAX_SECONDS)
        return None

    log.info("viral hook: using %r (%.1fs) from %s", slug, dur, url)
    return out


def fetch_hook_for_topic(topic: str, dest_dir: Path) -> Path | None:
    """Return a local hook MP4 chosen for `topic`, or None (never raises)."""
    try:
        if (os.getenv("DISABLE_VIRAL_HOOK") or "").strip().lower() in ("1", "true", "yes"):
            log.info("viral hook: DISABLE_VIRAL_HOOK set — skipping opener.")
            return None

        order: list[str]
        forced = (os.getenv("VIRAL_HOOK_SLUG") or "").strip().lower()
        if forced:
            order = [forced]
        else:
            order = list(_catalog())
            # Seeded shuffle: same Topic -> same hook every re-run; different
            # topics land on different hooks. Later entries are the retry
            # order when the first pick won't download.
            random.Random((topic or "").strip().lower()).shuffle(order)

        for slug in order[:3]:
            got = _download(slug, dest_dir)
            if got is not None:
                return got
        log.warning("viral hook: no usable hook after %d attempt(s) — "
                    "building without an opener.", min(3, len(order)))
        return None
    except Exception as exc:  # noqa: BLE001 — the hook must never kill a build
        log.warning("viral hook: unexpected error (%s) — building without "
                    "an opener.", exc)
        return None
