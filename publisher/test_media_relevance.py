"""Regression tests for the media picker's two most-reported failures:

  1. WRONG COMPANY — a topic about Anthropic pulling an OpenAI clip (because the
     OpenAI title shared generic words like "dangerous model"). Fixed by a hard
     rival-company block in scoring._rival_brand_signal.
  2. OLD FOOTAGE — a "just released" topic pulling months-old footage (the model
     "has been pushed for months"). Fixed by scoring._recency_signal.

These mirror the exact example the user gave ("Anthropic just released its most
dangerous model" -> a GPT-4.6 clip). If either guard regresses, these fail.

Run:  python -m pytest publisher/test_media_relevance.py -q
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow running directly (python publisher/test_media_relevance.py) as well as
# via pytest from the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from publisher.media_sources import scoring as s  # noqa: E402

NOW = datetime(2026, 6, 23, tzinfo=timezone.utc)

# Official channel ids straight from brand_detect.BRANDS — keep in sync.
ANTHROPIC_CH = "UCrDwWp7EBBv4NwvScIpBDOA"
OPENAI_CH = "UCXZCJLdBC09xxGZ6gcdrc6A"

TOPIC = "Anthropic just released its most dangerous model"


def _score(c: dict, brands: list[str], topic: str = TOPIC) -> int:
    """Score with a fixed 'now' so recency is deterministic."""
    orig = s._recency_signal
    s._recency_signal = lambda cand: orig(cand, now=NOW)  # type: ignore
    try:
        return s.score(c, matched_brands=brands, topic=topic)
    finally:
        s._recency_signal = orig  # type: ignore


def _pick(cands: list[dict], brands: list[str], topic: str = TOPIC):
    orig = s._recency_signal
    s._recency_signal = lambda cand: orig(cand, now=NOW)  # type: ignore
    try:
        return s.pick_best(cands, matched_brands=brands, topic=topic)
    finally:
        s._recency_signal = orig  # type: ignore


def _yt(title, channel, channel_id, published_at=None, dur=40):
    return {
        "source": "youtube", "kind": "video", "title": title,
        "media_url": f"https://youtube.com/watch?v={abs(hash(title)) % 10**8}",
        "duration_s": dur,
        "extra": {"channel": channel, "channel_id": channel_id,
                  "published_at": published_at},
    }


def _pexels(title="Abstract tech background"):
    return {
        "source": "pexels_video", "kind": "video", "title": title,
        "media_url": f"https://pexels.com/v/{abs(hash(title)) % 10**8}",
        "duration_s": 15, "extra": {},
    }


def test_rival_company_clip_is_blocked():
    """An OpenAI clip must NOT win an Anthropic topic, even with matching words."""
    openai_clip = _yt("The Most Dangerous AI Model Yet - What It Can Do",
                      "OpenAI", OPENAI_CH, "2025-12-01T00:00:00Z")
    assert _score(openai_clip, ["anthropic"]) < 0


def test_correct_company_fresh_clip_wins():
    openai_old = _yt("The Most Dangerous AI Model Yet",
                     "OpenAI", OPENAI_CH, "2025-12-01T00:00:00Z")
    anthropic_fresh = _yt(
        "Claude Opus 4.8 - our most capable and most dangerous model",
        "Anthropic", ANTHROPIC_CH, "2026-06-20T00:00:00Z")
    winner, _ = _pick([openai_old, anthropic_fresh, _pexels()], ["anthropic"])
    assert winner["extra"].get("channel") == "Anthropic"


def test_neutral_fallback_beats_wrong_company():
    """If the only options are a rival clip + neutral stock, stock wins —
    never show the wrong company."""
    openai_clip = _yt("The Most Dangerous AI Model Yet", "OpenAI", OPENAI_CH,
                      "2026-06-20T00:00:00Z")  # even if fresh, wrong company
    winner, _ = _pick([openai_clip, _pexels()], ["anthropic"])
    assert winner["source"] == "pexels_video"


def test_no_brand_topic_does_not_block_anyone():
    """A topic naming no brand must not trigger the rival block."""
    generic = "The most dangerous AI model just dropped"
    openai_clip = _yt("The Most Dangerous AI Model Yet", "OpenAI", OPENAI_CH,
                      "2026-06-20T00:00:00Z")
    assert _score(openai_clip, [], topic=generic) > 0


def test_same_company_official_not_blocked():
    official = {
        "source": "anthropic_official", "kind": "video",
        "title": "Introducing Claude Opus 4.8",
        "media_url": "https://youtube.com/watch?v=a2", "duration_s": 50,
        "extra": {"channel": "Anthropic", "channel_id": ANTHROPIC_CH,
                  "published_at": "2026-06-20T00:00:00Z"},
    }
    assert _score(official, ["anthropic"]) > 100


def test_fresh_clip_outranks_stale_same_company():
    """Two on-topic Anthropic clips: the recent one must win."""
    stale = _yt("Claude most dangerous model", "Anthropic", ANTHROPIC_CH,
                "2025-06-01T00:00:00Z")   # ~1 year old
    fresh = _yt("Claude most dangerous model", "Anthropic", ANTHROPIC_CH,
                "2026-06-20T00:00:00Z")   # days old
    assert _score(fresh, ["anthropic"]) > _score(stale, ["anthropic"])


def test_undated_clip_not_penalized_for_recency():
    """A clip we can't date keeps a neutral recency contribution."""
    undated = _yt("Claude Opus 4.8 most dangerous model", "Some Reviewer",
                  "UNKNOWN", published_at=None)
    dated_recent = _yt("Claude Opus 4.8 most dangerous model", "Some Reviewer",
                       "UNKNOWN", "2026-06-20T00:00:00Z")
    # Recent should score higher, but undated should NOT be sunk below an
    # off-topic floor — it stays a genuine contender.
    assert _score(undated, ["anthropic"]) > 100
    assert _score(dated_recent, ["anthropic"]) >= _score(undated, ["anthropic"])


# --- Query-builder tests: search the LITERAL topic, don't paraphrase --------
# The user's locked rule: "put the keyword into YouTube just like that." The
# real subject + headline words must LEAD the search; we must not rewrite the
# topic into vague concept words (the old "Anthropic released dangerous AI
# safety" bug).

def test_query_leads_with_literal_subject():
    from publisher.media_finder import _build_query
    q = _build_query("Anthropic just released its most dangerous model", "")
    ql = q.lower()
    # The real subject + distinctive words must be present and lead.
    assert ql.startswith("anthropic")
    assert "dangerous" in ql and "model" in ql
    # Pure filler must be gone.
    for filler in (" just ", " its ", " most "):
        assert filler not in f" {ql} "


def test_query_keeps_version_number():
    from publisher.media_finder import _build_query
    q = _build_query("Claude Opus 4.8: The Most Dangerous Model Yet", "").lower()
    assert "claude" in q and "4.8" in q          # version is the decisive signal
    assert q.count("claude") == 1                 # no duplicate "Claude Claude"
    assert q.count("4.8") == 1


def test_query_no_duplicate_words():
    from publisher.media_finder import _build_query
    for topic in ("Anthropic Is Now Worth Almost $1 Trillion",
                  "Claude Opus 4.8: The Most Dangerous Model Yet"):
        words = _build_query(topic, "").lower().split()
        assert len(words) == len(set(words)), f"dup in {topic!r}: {words}"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL  {fn.__name__}: {exc}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
