"""publisher/media_sources/word_timing.py — per-WORD timestamps for a YouTube
video, free, for the motivation-speech reel format.

The motivation format burns word-by-word captions onto the video, so it needs
to know WHEN each word is spoken. YouTube's auto-captions already carry that:
in the `json3` subtitle format every caption event lists its words as `segs`,
each with a `tOffsetMs` relative to the event's `tStartMs`. yt-dlp can fetch
that file without downloading the video — zero new dependencies, works on both
runners (ubuntu cloud + the self-hosted Windows PC).

Fallback: when json3 isn't available but plain VTT cues are, each cue's words
are spread across the cue's [start, end] proportionally to word length. Less
precise (±0.3s) but visually fine for 2-3 word caption pages.

Contract mirrors transcript_picker: returns [] on any failure, never raises on
network noise. PROXY_URL is passed to yt-dlp ONLY (never os.environ — that
breaks Sheets/Drive auth).
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any

log = logging.getLogger("media_sources.word_timing")

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Reuse the sibling's cookie/VTT machinery — one implementation of the
# YouTube-caption fetch quirks, not two.
try:
    from publisher.media_sources.transcript_picker import (
        _download_vtt, _youtube_cookiefile, parse_vtt_cues,
    )
except Exception:  # noqa: BLE001 — same import-style resilience as siblings
    try:
        from media_sources.transcript_picker import (  # type: ignore
            _download_vtt, _youtube_cookiefile, parse_vtt_cues,
        )
    except Exception:  # noqa: BLE001
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent))
        from transcript_picker import (  # type: ignore
            _download_vtt, _youtube_cookiefile, parse_vtt_cues,
        )

# Non-speech caption tokens: [music], [applause], (laughter) — never burn these.
_NOISE_WORD_RE = re.compile(r"^\[.*\]$|^\(.*\)$|^♪+$")


def _clean_word(raw: str) -> str:
    """Normalize one caption segment into a printable word ('' = drop it)."""
    w = (raw or "").replace("\n", " ").strip()
    if not w or _NOISE_WORD_RE.match(w):
        return ""
    return w


def _parse_json3(text: str) -> list[dict]:
    """Parse a YouTube json3 subtitle blob into [{word, start, end}] seconds."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return []
    words: list[dict] = []
    for ev in data.get("events") or []:
        t0 = ev.get("tStartMs")
        segs = ev.get("segs")
        if t0 is None or not segs:
            continue
        dur = ev.get("dDurationMs") or 0
        for seg in segs:
            w = _clean_word(seg.get("utf8", ""))
            if not w:
                continue
            start = (t0 + (seg.get("tOffsetMs") or 0)) / 1000.0
            words.append({"word": w, "start": start,
                          "end": (t0 + dur) / 1000.0 if dur else start + 0.6})
    words.sort(key=lambda x: x["start"])
    # A word ends when the next one starts (capped so a long pause doesn't
    # stretch the last word of a sentence across the silence).
    for i, w in enumerate(words):
        if i + 1 < len(words):
            w["end"] = min(max(words[i + 1]["start"], w["start"] + 0.08),
                           w["start"] + 1.5)
        else:
            w["end"] = min(w["end"], w["start"] + 1.5)
            if w["end"] <= w["start"]:
                w["end"] = w["start"] + 0.6
    return words


def _download_json3(video_url: str) -> str:
    """Fetch the raw json3 auto/manual captions via yt-dlp. '' on failure.
    Same anti-bot-block + proxy treatment as transcript_picker._download_vtt."""
    try:
        from yt_dlp import YoutubeDL  # type: ignore
    except ImportError:
        log.warning("yt-dlp not installed; cannot fetch word timings.")
        return ""

    with tempfile.TemporaryDirectory() as tmp:
        ydl_opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": ["en", "en-US", "en-GB", "en-orig"],
            "subtitlesformat": "json3",
            "noplaylist": True,
            "outtmpl": str(Path(tmp) / "%(id)s.%(ext)s"),
            "remote_components": ["ejs:github"],
        }
        cookiefile = _youtube_cookiefile()
        if cookiefile:
            ydl_opts["cookiefile"] = cookiefile
        proxy = os.environ.get("PROXY_URL", "").strip()
        if proxy:
            ydl_opts["proxy"] = proxy  # yt-dlp only — never os.environ

        try:
            with YoutubeDL(ydl_opts) as ydl:
                ydl.download([video_url])
        except Exception as exc:  # noqa: BLE001 — no captions / blocked / gone
            log.info("No json3 captions for %s (%s)", video_url, exc)
            return ""

        files = sorted(Path(tmp).glob("*.json3"))
        if not files:
            log.info("No json3 caption file produced for %s", video_url)
            return ""
        try:
            return files[0].read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ""


def _even_split_fallback(video_url: str) -> list[dict]:
    """VTT cues → per-word timing by spreading each cue's words across its
    [start, end] proportionally to word length. Precision ~±0.3s."""
    raw = _download_vtt(video_url)
    if not raw:
        return []
    cues = parse_vtt_cues(raw)
    words: list[dict] = []
    for cue in cues:
        toks = [w for w in (
            _clean_word(t) for t in str(cue.get("text", "")).split()
        ) if w]
        if not toks:
            continue
        start = float(cue.get("start") or 0.0)
        end = float(cue.get("end") or start)
        span = max(end - start, 0.12 * len(toks))
        total_chars = sum(len(t) for t in toks) or 1
        t = start
        for tok in toks:
            share = span * len(tok) / total_chars
            words.append({"word": tok, "start": t, "end": t + share})
            t += share
    words.sort(key=lambda x: x["start"])
    return words


def fetch_word_timings(video_url: str) -> list[dict]:
    """Best word-level timing available for `video_url`, as
    [{word, start, end}, ...] in seconds from video t=0 (the motivation build
    downloads from t=0, so these map 1:1 onto reel time).

    json3 (true per-word) first; VTT even-split as fallback. Returns [] only
    when the video has no fetchable English captions at all. Never raises on
    network/caption noise."""
    words = _parse_json3(_download_json3(video_url))
    if words:
        log.info("Word timings: json3, %d words (%.1fs → %.1fs)",
                 len(words), words[0]["start"], words[-1]["end"])
        return words
    words = _even_split_fallback(video_url)
    if words:
        log.info("Word timings: VTT even-split fallback, %d words", len(words))
    else:
        log.warning("No word timings available for %s", video_url)
    return words
