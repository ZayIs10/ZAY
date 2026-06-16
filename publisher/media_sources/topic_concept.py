"""Detect the CONCEPT (story type) of a reel topic, so media matching can
follow the SUBJECT of the news — not just literal title words.

The bug this addresses: a topic like "Anthropic Is Now Worth Almost $1
Trillion" is a VALUATION story. Its only distinctive title words are
"almost/worth/trillion" — which appear in NO real clip title, so the keyword
scorer ties every candidate and a random Claude product demo wins. But the
clip should show the *concept*: funding / valuation / money / the company's
growth — never a product walkthrough.

So we classify the topic into one or more concepts, each carrying:
  - `query_terms`: words to ADD to the search so the candidate pool actually
    contains concept-relevant footage (e.g. "funding valuation billion").
  - `title_terms`: words whose presence in a candidate TITLE proves it's
    on-concept (used by scoring to reward the right clip).

Deterministic keyword matching — no LLM, no network. Free and fast.
"""

from __future__ import annotations

import re

# Each concept: a set of trigger words/patterns found in the topic, and the
# terms that make a clip on-concept. Order matters only for the primary-concept
# pick (first match wins as "primary"); all matches contribute terms.
CONCEPTS: list[dict] = [
    {
        "name": "valuation",
        # Money / worth / funding milestone stories.
        "triggers": [
            "valuation", "valued", "worth", "trillion", "billion", "million",
            "raises", "raise", "funding", "funded", "round", "investment",
            "invests", "invested", "ipo", "market cap", "stake", "backed",
            "richest", "net worth",
        ],
        "query_terms": "funding valuation billion investment news",
        "title_terms": [
            "valuation", "valued", "worth", "trillion", "billion", "funding",
            "raises", "raise", "investment", "invests", "ipo", "stake",
            "market", "cap", "backed", "round",
        ],
    },
    {
        "name": "partnership",
        "triggers": [
            "partners", "partnership", "deal", "acquires", "acquisition",
            "buys", "merger", "teams up", "joins forces", "collaboration",
            "signs",
        ],
        "query_terms": "partnership deal announcement news",
        "title_terms": [
            "partner", "partnership", "deal", "acquires", "acquisition",
            "buys", "merger", "collaboration", "signs",
        ],
    },
    {
        "name": "safety_risk",
        "triggers": [
            "danger", "dangerous", "risk", "risky", "too risky", "safety",
            "unsafe", "threat", "warns", "warning", "scary", "ban", "banned",
            "shut down", "rogue", "out of control",
        ],
        "query_terms": "AI safety risk warning news",
        "title_terms": [
            "safety", "risk", "danger", "dangerous", "threat", "warns",
            "warning", "ban", "rogue", "control",
        ],
    },
    {
        "name": "legal",
        "triggers": [
            "lawsuit", "sues", "sued", "court", "legal", "settlement",
            "settles", "fraud", "sec", "regulation", "regulator", "fine",
            "fined", "antitrust",
        ],
        "query_terms": "lawsuit court legal news",
        "title_terms": [
            "lawsuit", "sues", "sued", "court", "legal", "settlement",
            "settles", "fraud", "sec", "regulation", "fine", "antitrust",
        ],
    },
    {
        "name": "benchmark",
        # Performance / test score / capability milestone stories.
        # "Claude Scored 97% on the Math Olympiad", "GPT-5 Aces the Bar Exam",
        # "Gemini Beats GPT-4 on Coding". Separate from launch so the query
        # points at competition/test footage, not product-demo footage.
        "triggers": [
            "scored", "scores", "score", "benchmark", "benchmarks",
            "beats", "beat", "outperforms", "outperformed", "surpasses",
            "surpassed", "tops", "aces", "passed", "passes", "accuracy",
            "state-of-the-art", "sota", "math olympiad", "olympiad",
            "exam", "test", "competition", "record", "milestone",
            "performance", "percent", "%",
        ],
        "query_terms": "benchmark test performance score",
        "title_terms": [
            "benchmark", "score", "test", "performance", "beats", "outperforms",
            "accuracy", "exam", "olympiad", "record", "milestone", "percent",
        ],
    },
    {
        "name": "launch",
        # A product/model release or capability — the DEMO case. Last so the
        # more specific business concepts above win as primary when present.
        "triggers": [
            "launch", "launches", "introducing", "releases", "released",
            "drops", "dropped", "unveils", "announces", "new model",
            "now does", "can now", "update", "feature", "builds", "runs",
            "writes", "codes", "generates", "just dropped", "just released",
        ],
        "query_terms": "demo launch feature",
        "title_terms": [
            "launch", "introducing", "release", "unveil", "demo", "feature",
            "preview", "first look", "hands on", "walkthrough",
        ],
    },
]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def detect_concepts(topic: str, key_points: str = "") -> list[dict]:
    """Return the matching CONCEPTS (full dicts) for a topic, most-specific
    first. Empty list if nothing matches (caller falls back to plain
    keyword/brand behavior)."""
    hay = _norm(f"{topic} {key_points}")
    hits: list[dict] = []
    for concept in CONCEPTS:
        if any(t in hay for t in concept["triggers"]):
            hits.append(concept)
    return hits


def concept_query_terms(topic: str, key_points: str = "") -> str:
    """Extra search terms (deduped, order-preserving) to widen the candidate
    pool toward the story's concept. '' when no concept matched."""
    seen: set[str] = set()
    out: list[str] = []
    for concept in detect_concepts(topic, key_points):
        for w in concept["query_terms"].split():
            if w not in seen:
                seen.add(w)
                out.append(w)
    return " ".join(out)


def concept_title_terms(topic: str, key_points: str = "") -> set[str]:
    """Set of terms whose presence in a candidate TITLE marks it on-concept.
    Empty set when no concept matched."""
    terms: set[str] = set()
    for concept in detect_concepts(topic, key_points):
        terms.update(concept["title_terms"])
    return terms


def primary_concept(topic: str, key_points: str = "") -> str:
    """Name of the most-specific matching concept, or '' if none."""
    hits = detect_concepts(topic, key_points)
    return hits[0]["name"] if hits else ""
