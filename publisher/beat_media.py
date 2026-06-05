"""Find one background clip PER BEAT for the multi-beat reel format.

Where media_finder picks a single winner for the whole reel, this picks a
DISTINCT clip for each beat so the footage cuts on every text reveal (the
@evolving.ai look). It reuses the same keyless/API sources and the
demo-first scoring, just run per beat with beat-specific search terms.

Strategy per reel:
  1. Detect the brand from the topic (once).
  2. For each beat, search videos seeded by the beat's text (+ topic +
     brand channel), score with the demo-first scorer, take the top clip
     not already used by an earlier beat.
  3. If a beat finds nothing new, fall back to reusing an earlier beat's
     clip (so one weak beat doesn't sink the reel). Only if ZERO beats
     across the whole reel find any clip do we report failure (-> the
     caller skips the post; no stills).
"""

from __future__ import annotations

import logging
import re

from publisher.beats import Beat
from publisher.media_sources import brand_detect, brand_official, youtube
from publisher.media_sources.scoring import rank

log = logging.getLogger("beat_media")

_STOP = {
    "the", "a", "an", "and", "or", "but", "to", "of", "in", "on", "for",
    "with", "just", "it", "its", "is", "was", "into", "that", "this",
    "when", "who", "they", "you", "your", "next", "one", "put", "picked",
    "move", "moves", "pay", "attention", "names", "biggest", "drop",
    "follow", "wasnt", "wasn", "t",
}


def _keywords(text: str, limit: int = 4) -> str:
    """Pull the most search-worthy words from a beat line: keep proper
    nouns / numbers / longer tokens, drop stopwords. Falls back to the
    raw text if nothing survives."""
    toks = re.findall(r"[A-Za-z0-9$%]+", text)
    keep: list[str] = []
    for t in toks:
        low = t.lower().strip("$%")
        if low in _STOP or len(low) <= 1:
            continue
        keep.append(t)
        if len(keep) >= limit:
            break
    return " ".join(keep) if keep else text.strip()


def _beat_query(beat: Beat, topic: str, brand: str) -> str:
    """Compose a per-beat video search query. Bias toward the brand + the
    beat's own keywords so each beat surfaces different footage."""
    kw = _keywords(beat.text)
    brand_hint = brand if brand else ""
    # The reel's subject (topic) anchors every beat; the beat keywords
    # differentiate. Keep it short — long queries returned 0 from the API.
    parts = [p for p in (brand_hint, kw) if p]
    return " ".join(parts).strip() or topic.strip()


def find_beat_clips(
    beats: list[Beat], topic: str, key_points: str = "",
) -> list[dict | None]:
    """Return a list parallel to `beats`: each entry is a winning video
    Candidate dict (with media_url) for that beat, or None if even the
    reuse fallback couldn't supply one (only when nothing was found at all).
    """
    if not beats:
        return []

    matched = brand_detect.detect_brands(f"{topic} {key_points}")
    brand = matched[0] if matched else ""
    brand_cfg = brand_detect.brand_config(brand) if brand else {}
    channel_id = brand_cfg.get("youtube_handle_id")
    channel_handle = brand_cfg.get("youtube_channel")

    used_urls: set[str] = set()
    winners: list[dict | None] = []

    for beat in beats:
        query = _beat_query(beat, topic, brand)
        log.info("Beat %d query: %r", beat.index, query)

        candidates: list[dict] = []
        # Official channel first (premium, on-brand), then general.
        if brand:
            try:
                candidates += brand_official.search_videos(brand, query, limit=6)
            except Exception as exc:  # noqa: BLE001
                log.warning("beat %d official search failed: %s", beat.index, exc)
        try:
            candidates += youtube.search_videos(
                query, limit=6,
                channel_handle=channel_handle, channel_id=channel_id,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("beat %d general search failed: %s", beat.index, exc)

        ranked = rank(candidates, matched_brands=matched)
        # Pick the best clip not already used by an earlier beat.
        pick = None
        for c in ranked:
            url = c.get("media_url") or c.get("page_url")
            if url and url not in used_urls:
                pick = c
                break
        if pick:
            used_urls.add(pick.get("media_url") or pick.get("page_url"))
            winners.append(pick)
            log.info("Beat %d -> %s", beat.index,
                     (pick.get("title") or "")[:60])
        else:
            winners.append(None)
            log.info("Beat %d -> no fresh clip", beat.index)

    # Reuse fallback: fill any None beat by cycling through the clips we DID
    # find, so every beat has footage as long as at least one was found.
    found = [w for w in winners if w]
    if found:
        ri = 0
        for i, w in enumerate(winners):
            if w is None:
                winners[i] = found[ri % len(found)]
                ri += 1
                log.info("Beat %d -> reused %s", i,
                         (winners[i].get("title") or "")[:50])

    return winners
