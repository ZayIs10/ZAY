"""publisher/media_sources/transcript_picker.py — pick the YouTube video whose
TRANSCRIPT best matches a topic, for rows that have NO hand-picked YouTube URL.

The problem this solves
-----------------------
`research_topic.py` used to fill the "YouTube URL" column with whatever came
FIRST in a YouTube search — a title-only guess. Titles lie: a clip titled
"Claude builds a game" might spend its 12 minutes on something off-topic. The
only ground truth for what a video is actually ABOUT is its spoken content, i.e.
the transcript.

So when (and ONLY when) a row has no YouTube URL yet, we:
  1. Search YouTube for the topic (reusing media_sources.youtube — API when a
     key is set, yt-dlp scrape otherwise; both handle CI bot-blocking).
  2. Download each candidate's TRANSCRIPT for free via yt-dlp (auto-captions,
     no API key, reuses the same cookies the download path uses).
  3. SCORE each transcript against the topic keywords using the existing free,
     deterministic keyword/version logic from scoring.py (no LLM — the OpenAI
     quota is dead and this must stay $0).
  4. Return the highest-scoring video + its transcript text so the caller can
     write the URL back and ground the captions in real spoken content.

Everything here is free and offline-friendly: no paid API, no OpenAI.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any

log = logging.getLogger("media_sources.transcript_picker")

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Reuse the exact keyword/version splitter the media scorer uses, so a
# transcript is judged on the SAME distinctive tokens (version numbers, product
# names) that pick the background clip — one relevance definition, not two.
try:  # package import (media_finder runs with repo root on sys.path)
    from publisher.media_sources.scoring import topic_keywords
except Exception:  # noqa: BLE001 — flat import when run from inside the pkg dir
    from scoring import topic_keywords  # type: ignore


# --- transcript scoring weights -------------------------------------------
# Deterministic, mirrors scoring.py's philosophy: distinctive terms and version
# numbers dominate; generic niche words ("AI", "Claude") are already stripped by
# topic_keywords(). A transcript that never says the topic's distinctive words
# is off-topic and scores ~0.
TERM_HIT_POINTS = 6          # per distinctive topic word that appears at all
TERM_FREQUENCY_CAP = 4       # count a repeated word at most this many times...
TERM_FREQUENCY_POINTS = 2    # ...at this many points each (rewards focus)
VERSION_HIT_POINTS = 40      # transcript says the exact version (4.8, gpt-5) — decisive
TITLE_TERM_POINTS = 4        # small extra weight when the TITLE also carries a term
COVERAGE_BONUS = 25          # transcript hits EVERY distinctive topic word
NO_TRANSCRIPT_SCORE = -1     # candidates we couldn't transcribe sort below any real one


def _youtube_cookiefile() -> str | None:
    """Locate a Netscape-format YouTube cookies file (mirrors youtube.py).

    YouTube bot-blocks datacenter IPs; cookies unblock both the search scrape
    and subtitle fetch. Kept local so this light module doesn't import the
    heavier media_consumer.
    """
    env_path = os.getenv("YOUTUBE_COOKIES_FILE", "").strip()
    candidates = [env_path] if env_path else []
    candidates.append(str(_REPO_ROOT / "youtube_cookies.txt"))
    for c in candidates:
        if c and Path(c).exists() and Path(c).stat().st_size > 0:
            return c
    return None


def _strip_vtt(vtt_text: str) -> str:
    """Turn a WebVTT/SRT subtitle blob into plain, de-duplicated spoken text.

    Auto-caption VTT repeats lines as they scroll (rolling captions), so naive
    concatenation triples every word. We drop cue-timing lines, tags, and
    consecutive duplicate lines.
    """
    out: list[str] = []
    last = ""
    for raw in vtt_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line == "WEBVTT" or line.startswith("Kind:") or line.startswith("Language:"):
            continue
        # Cue timing line: "00:00:01.000 --> 00:00:03.000 ..."
        if "-->" in line:
            continue
        # Pure cue index (SRT) or NOTE lines
        if line.isdigit() or line.startswith("NOTE"):
            continue
        # Strip inline timestamp/formatting tags: <00:00:01.000>, <c>, </c>
        line = re.sub(r"<[^>]+>", "", line).strip()
        if not line or line == last:
            continue
        out.append(line)
        last = line
    return " ".join(out)


def fetch_transcript(video_url: str, *, max_chars: int = 20000) -> str:
    """Download `video_url`'s transcript for free via yt-dlp auto-captions.

    Returns plain text (possibly truncated to `max_chars`), or "" if the video
    has no fetchable captions. Never raises — a failure here just means that
    candidate can't be transcript-scored, not that the whole run breaks.
    """
    try:
        from yt_dlp import YoutubeDL  # type: ignore
    except ImportError:
        log.warning("yt-dlp not installed; cannot fetch transcripts.")
        return ""

    with tempfile.TemporaryDirectory() as tmp:
        outtmpl = str(Path(tmp) / "%(id)s.%(ext)s")
        ydl_opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,          # captions only, never the video
            "writesubtitles": True,         # manual subs when the uploader added them
            "writeautomaticsub": True,      # else YouTube's auto-generated ones
            "subtitleslangs": ["en", "en-US", "en-GB", "en-orig"],
            "subtitlesformat": "vtt",
            "noplaylist": True,
            # Same anti-bot-block treatment the download/search paths use.
            "remote_components": ["ejs:github"],
        }
        cookiefile = _youtube_cookiefile()
        if cookiefile:
            ydl_opts["cookiefile"] = cookiefile

        try:
            with YoutubeDL(ydl_opts) as ydl:
                ydl.download([video_url])
        except Exception as exc:  # noqa: BLE001 — no captions / blocked / gone
            log.info("No transcript for %s (%s)", video_url, exc)
            return ""

        # yt-dlp writes <id>.<lang>.vtt — grab the first English one it produced.
        vtts = sorted(Path(tmp).glob("*.vtt"))
        if not vtts:
            log.info("No transcript file produced for %s", video_url)
            return ""
        try:
            raw = vtts[0].read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ""

    text = _strip_vtt(raw)
    if len(text) > max_chars:
        text = text[:max_chars]
    return text


def score_transcript(transcript: str, title: str, topic: str,
                     context: str = "") -> tuple[int, dict]:
    """Score how well a transcript matches the topic. Free + deterministic.

    Returns (score, detail) where detail explains the breakdown (handy for
    logging why one video beat another). Higher score = better match. A
    transcript that never mentions the topic's distinctive words scores ~0.
    """
    words, versions = topic_keywords(topic)
    if context:
        ctx_words, _ = topic_keywords(context)
        words = words | ctx_words
    if not words and not versions:
        # Nothing distinctive to match on — fall back to "has a transcript at all".
        return (1 if transcript else NO_TRANSCRIPT_SCORE), {"reason": "no keywords"}

    if not transcript:
        return NO_TRANSCRIPT_SCORE, {"reason": "no transcript"}

    text_l = transcript.lower()
    title_l = (title or "").lower()

    score = 0
    hit_terms: list[str] = []
    for w in words:
        count = text_l.count(w)
        if count:
            hit_terms.append(w)
            score += TERM_HIT_POINTS
            score += min(count, TERM_FREQUENCY_CAP) * TERM_FREQUENCY_POINTS
            if w in title_l:
                score += TITLE_TERM_POINTS

    hit_versions: list[str] = []
    for v in versions:
        # Version tokens (4.8, gpt-5) matched as whole tokens so "4.8" doesn't
        # match inside "14.85". Punctuation-flexible: spoken "four point eight"
        # won't match, but the on-screen/title form usually appears in captions.
        if re.search(rf"(?<![\w.]){re.escape(v)}(?![\w.])", text_l):
            hit_versions.append(v)
            score += VERSION_HIT_POINTS

    if words and len(hit_terms) == len(words):
        score += COVERAGE_BONUS

    detail = {
        "hit_terms": hit_terms,
        "hit_versions": hit_versions,
        "total_terms": len(words),
        "total_versions": len(versions),
        "chars": len(transcript),
    }
    return score, detail


def pick_best_by_transcript(
    topic: str,
    *,
    context: str = "",
    max_candidates: int = 5,
    channel_id: str | None = None,
) -> dict | None:
    """Search YouTube for `topic`, transcript-score each candidate, return the
    best one. Returns None when nothing scores above the no-transcript floor.

    Return shape:
        {
          "url": "https://www.youtube.com/watch?v=...",
          "title": "...",
          "channel": "...",
          "score": 128,
          "transcript": "full spoken text (truncated)",
          "detail": {...scoring breakdown...},
          "considered": [ {title, url, score}, ... ],  # for logging
        }
    """
    try:  # package or flat import, same as scoring.py does
        from publisher.media_sources.youtube import search_videos
    except Exception:  # noqa: BLE001
        from youtube import search_videos  # type: ignore

    candidates = search_videos(topic, limit=max_candidates, channel_id=channel_id)
    if not candidates:
        log.warning("Transcript picker: no YouTube candidates for %r", topic)
        return None

    scored: list[dict] = []
    for cand in candidates:
        url = cand.get("page_url") or cand.get("media_url") or ""
        if not url:
            continue
        title = cand.get("title") or ""
        transcript = fetch_transcript(url)
        s, detail = score_transcript(transcript, title, topic, context)
        scored.append({
            "url": url,
            "title": title,
            "channel": (cand.get("extra") or {}).get("channel"),
            "score": s,
            "transcript": transcript,
            "detail": detail,
        })
        log.info("  transcript-score %d  %r  (terms %s, versions %s)",
                 s, title[:60],
                 detail.get("hit_terms"), detail.get("hit_versions"))

    if not scored:
        return None

    scored.sort(key=lambda c: c["score"], reverse=True)
    best = scored[0]

    if best["score"] <= NO_TRANSCRIPT_SCORE:
        # Not one candidate had a usable, on-topic transcript. Don't force a
        # bad pick — let the caller leave YouTube URL blank so the build-time
        # media_finder does its own (title-based) search instead.
        log.warning("Transcript picker: no on-topic transcript found for %r", topic)
        return None

    best["considered"] = [
        {"title": c["title"], "url": c["url"], "score": c["score"]}
        for c in scored
    ]
    log.info("Transcript picker WINNER (%d): %s", best["score"], best["url"])
    return best
