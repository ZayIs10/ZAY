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
# Robust across every import style callers use (repo root on path; publisher/
# on path via research_topic; or inside the pkg dir): try each, and as a final
# fallback add this module's own directory so `scoring` always resolves.
try:  # 1. package import (media_finder runs with repo root on sys.path)
    from publisher.media_sources.scoring import topic_keywords
except Exception:  # noqa: BLE001
    try:  # 2. publisher/ on sys.path (research_topic.py)
        from media_sources.scoring import topic_keywords  # type: ignore
    except Exception:  # noqa: BLE001
        try:  # 3. media_sources/ itself on sys.path
            from scoring import topic_keywords  # type: ignore
        except Exception:  # noqa: BLE001 — 4. self-heal: add our dir, retry
            import sys as _sys
            _sys.path.insert(0, str(Path(__file__).resolve().parent))
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
# WRONG-VERSION penalty: the topic names version X (4.8) but the transcript
# never says X and instead prominently features a DIFFERENT version of the same
# product (4.5). That's a video about the OLD model — sink it below any clip
# that does say the right version. (Real bug caught on a live run: a topic about
# "Opus 4.8" picked an "Opus 4.5" video that won on generic words.)
WRONG_VERSION_PENALTY = 45
# A "same family" version looks like the topic's version with the minor bumped
# down/up, e.g. topic 4.8 vs transcript 4.5/4.6/4.7 — same product, wrong release.
_FAMILY_VERSION_RE = re.compile(r"\b(\d+)\.\d+\b")


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


def _vtt_ts_to_seconds(ts: str) -> float:
    """Parse a VTT/SRT timestamp 'HH:MM:SS.mmm' (or 'MM:SS.mmm') to seconds."""
    ts = ts.strip().replace(",", ".")
    parts = ts.split(":")
    try:
        if len(parts) == 3:
            h, m, s = parts
            return int(h) * 3600 + int(m) * 60 + float(s)
        if len(parts) == 2:
            m, s = parts
            return int(m) * 60 + float(s)
        return float(parts[0])
    except (ValueError, IndexError):
        return 0.0


_CUE_TIMING_RE = re.compile(
    r"(\d{1,2}:\d{2}:\d{2}[.,]\d{1,3}|\d{1,2}:\d{2}[.,]\d{1,3})\s*-->\s*"
    r"(\d{1,2}:\d{2}:\d{2}[.,]\d{1,3}|\d{1,2}:\d{2}[.,]\d{1,3})"
)


def parse_vtt_cues(vtt_text: str) -> list[dict]:
    """Parse a WebVTT/SRT blob into timed cues: [{start, end, text}, ...].

    Unlike _strip_vtt (which flattens to one string), this keeps the timing so
    the clip-window picker can find WHERE the topic payoff lands and cut on a
    real sentence/pause boundary. De-duplicates the rolling auto-caption repeats
    by dropping a cue whose text is contained in the previous cue's text.
    """
    cues: list[dict] = []
    cur_start: float | None = None
    cur_end: float | None = None
    cur_lines: list[str] = []

    def _flush() -> None:
        nonlocal cur_start, cur_end, cur_lines
        if cur_start is not None and cur_lines:
            text = " ".join(cur_lines).strip()
            if text:
                cues.append({"start": cur_start, "end": cur_end or cur_start,
                             "text": text})
        cur_start, cur_end, cur_lines = None, None, []

    for raw in vtt_text.splitlines():
        line = raw.strip()
        m = _CUE_TIMING_RE.search(line)
        if m:
            _flush()
            cur_start = _vtt_ts_to_seconds(m.group(1))
            cur_end = _vtt_ts_to_seconds(m.group(2))
            continue
        if not line or line == "WEBVTT" or line.startswith(("Kind:", "Language:", "NOTE")):
            continue
        if line.isdigit():  # SRT cue index
            continue
        clean = re.sub(r"<[^>]+>", "", line).strip()
        if clean:
            cur_lines.append(clean)
    _flush()

    # Collapse rolling-caption duplicates: auto-captions re-emit the tail of the
    # previous cue as the head of the next. Keep only the NEW text per cue.
    deduped: list[dict] = []
    prev_text = ""
    for c in cues:
        t = c["text"]
        if prev_text and t == prev_text:
            continue
        if prev_text and t.startswith(prev_text):
            t = t[len(prev_text):].strip()
        if t:
            deduped.append({"start": c["start"], "end": c["end"], "text": t})
            prev_text = c["text"]
    return deduped or cues


def _download_vtt(video_url: str) -> str:
    """Download `video_url`'s raw English VTT via yt-dlp. Returns the VTT text
    or "" on any failure (no captions / blocked / gone). Never raises."""
    try:
        from yt_dlp import YoutubeDL  # type: ignore
    except ImportError:
        log.warning("yt-dlp not installed; cannot fetch transcripts.")
        return ""

    with tempfile.TemporaryDirectory() as tmp:
        ydl_opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,          # captions only, never the video
            "writesubtitles": True,         # manual subs when the uploader added them
            "writeautomaticsub": True,      # else YouTube's auto-generated ones
            "subtitleslangs": ["en", "en-US", "en-GB", "en-orig"],
            "subtitlesformat": "vtt",
            "noplaylist": True,
            "outtmpl": str(Path(tmp) / "%(id)s.%(ext)s"),
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

        vtts = sorted(Path(tmp).glob("*.vtt"))
        if not vtts:
            log.info("No transcript file produced for %s", video_url)
            return ""
        try:
            return vtts[0].read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ""


def fetch_transcript(video_url: str, *, max_chars: int = 20000) -> str:
    """Download `video_url`'s transcript for free via yt-dlp auto-captions.

    Returns plain text (possibly truncated to `max_chars`), or "" if the video
    has no fetchable captions. Never raises — a failure here just means that
    candidate can't be transcript-scored, not that the whole run breaks.
    """
    raw = _download_vtt(video_url)
    if not raw:
        return ""
    text = _strip_vtt(raw)
    if len(text) > max_chars:
        text = text[:max_chars]
    return text


def fetch_transcript_cues(video_url: str) -> list[dict]:
    """Like fetch_transcript, but returns TIMED cues [{start, end, text}, ...]
    so callers can find where a moment lands and cut on a real boundary.
    Returns [] when no captions are available. Never raises."""
    raw = _download_vtt(video_url)
    if not raw:
        return []
    return parse_vtt_cues(raw)


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

    # WRONG-VERSION penalty. The topic pins a version (e.g. 4.8) but this
    # transcript never says it AND prominently talks about a DIFFERENT release
    # of the SAME product family (e.g. 4.5) — it's about the old model, not the
    # one we're posting about. Sink it so a right-version clip always wins.
    wrong_version = ""
    if versions and not hit_versions:
        # major numbers our topic versions belong to (4.8 -> "4")
        topic_majors = {m.group(1) for v in versions
                        for m in [_FAMILY_VERSION_RE.match(v)] if m}
        for fm in _FAMILY_VERSION_RE.finditer(text_l):
            token = fm.group(0)               # e.g. "4.5"
            if token in versions:
                continue                       # right version (shouldn't happen here)
            if fm.group(1) in topic_majors:    # same product family, wrong minor
                wrong_version = token
                score -= WRONG_VERSION_PENALTY
                break

    if words and len(hit_terms) == len(words):
        score += COVERAGE_BONUS

    detail = {
        "hit_terms": hit_terms,
        "hit_versions": hit_versions,
        "wrong_version": wrong_version,
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
    try:  # robust across every import style (see top-of-module scoring import)
        from publisher.media_sources.youtube import search_videos
    except Exception:  # noqa: BLE001
        try:
            from media_sources.youtube import search_videos  # type: ignore
        except Exception:  # noqa: BLE001
            import sys as _sys
            _sys.path.insert(0, str(Path(__file__).resolve().parent))
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
        wv = detail.get("wrong_version")
        log.info("  transcript-score %d  %r  (terms %s, versions %s%s)",
                 s, title[:60],
                 detail.get("hit_terms"), detail.get("hit_versions"),
                 f", WRONG-VERSION {wv}" if wv else "")

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
