"""Free, deterministic caption generator for reel rows.

Why this exists: a topic can be seeded with just Topic + Key Points +
Status="Ready to Run" (no captions). The build used to hard-fail such rows
with "missing required fields: Reel Caption / Post Caption". Instead, the
build now self-fills the captions here — the same way it self-finds the media.

NO OpenAI (the account's quota is dead — see project memory). Everything here
is string templating grounded in three free inputs:
  1. Topic            — the headline idea (always present on a Ready row)
  2. Key Points       — the one-line "what it teaches" (usually present)
  3. Video transcript — real spoken content from the row's YouTube URL, when
                        the row has one (fetched free via yt-dlp auto-captions)

Outputs match what tweet_card_reel.py consumes:
  - Reel Caption  : the words burned ON the video (short, punchy, NO hashtags)
  - Post Caption  : the IG text-box caption (keyword-first, follow CTA, ≤5 tags)

Brand voice (see CLAUDE.md + genz_brand_positioning.md): Gen Z Capital = "your
unfair AI advantage". Practical, fast, no hype, no emojis, neon-green energy.
"""

from __future__ import annotations

import re

# Follow CTA — brand rule: name the value, never bare "follow for more".
FOLLOW_CTA = (
    "Follow @genzcapital, I find the AI tools worth your time so you don't "
    "have to."
)

# A small, safe hashtag pool. We pick a few that match the topic so IG never
# sees a spammy >5 block. #AItools + #genz are brand-constant; the rest are
# added only when the topic mentions them.
BASE_TAGS = ["#AItools", "#AI"]
TAG_TRIGGERS = [
    (("chatgpt", "gpt", "openai"), "#ChatGPT"),
    (("claude", "anthropic"), "#Claude"),
    (("prompt",), "#prompting"),
    (("browser", "aside", "comet"), "#AIbrowser"),
    (("agent", "agentic"), "#AIagent"),
    (("automate", "automation", "workflow"), "#automation"),
    (("code", "coding", "developer", "app"), "#coding"),
    (("productiv", "faster", "hours"), "#productivity"),
]


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _first_sentences(text: str, n: int) -> list[str]:
    """Split into sentence-ish chunks and return the first n non-empty ones."""
    parts = re.split(r"(?<=[.!?])\s+", _clean(text))
    return [p.strip() for p in parts if p.strip()][:n]


def _pick_hashtags(topic: str, key_points: str) -> list[str]:
    blob = f"{topic} {key_points}".lower()
    tags = list(BASE_TAGS)
    for needles, tag in TAG_TRIGGERS:
        if any(nd in blob for nd in needles) and tag not in tags:
            tags.append(tag)
        if len(tags) >= 5:
            break
    return tags[:5]


def build_reel_caption(topic: str, key_points: str = "",
                       transcript: str = "") -> str:
    """The on-screen card text: 3-5 short lines, no hashtags, no emojis.

    Prefers real spoken lines from the transcript when we have them (grounds the
    reel in what the video actually says); otherwise builds from topic + points.
    """
    topic = _clean(topic)
    key_points = _clean(key_points)

    lines: list[str] = [topic.rstrip(".") + "."]

    # Ground the middle lines in the transcript if it's usable (a real
    # talking-head demo). Teasers with only music produce junk, so we require
    # a couple of reasonably long spoken sentences before trusting it.
    spoken = [s for s in _first_sentences(transcript, 6) if len(s) > 25]
    if len(spoken) >= 2:
        for s in spoken[:3]:
            if len(s) > 60:
                # Truncate on a WORD boundary — "your c..." mid-word cuts look
                # broken on the card.
                s = s[:57].rsplit(" ", 1)[0].rstrip(" ,;:") + "..."
            lines.append(s)
    elif key_points:
        # Split the key points into a couple of punchy beats. Break only on
        # STRONG separators (em/en dash, colon, semicolon, period) — never on
        # commas, so a comma-list like "Drive, Gmail, Notion" stays on one line
        # instead of fragmenting into one-word lines. A clause still too long
        # gets truncated at a word boundary.
        for s in re.split(r"\s*[—–:;.]\s*", key_points):
            s = s.strip()
            if not s:
                continue
            if len(s) > 52:
                s = s[:49].rsplit(" ", 1)[0].rstrip(",") + "..."
            lines.append(s)
            if len(lines) >= 4:
                break

    # Always land on a clean brand close line.
    lines.append("Here's how to use it.")
    # De-dupe + cap at 5 lines.
    out: list[str] = []
    for ln in lines:
        if ln and ln not in out:
            out.append(ln)
    return "\n".join(out[:5])


def build_post_caption(topic: str, key_points: str = "",
                       transcript: str = "") -> str:
    """The IG text-box caption: keyword-first, ~80-160 words, follow CTA, ≤5 tags.

    Brand rules: first sentence carries the core keyword (SEO signal), no emojis,
    end on a value-named follow CTA, then ≤5 hashtags on the final line.
    """
    topic = _clean(topic)
    key_points = _clean(key_points)

    # Opening sentence MUST contain the topic keyword (search signal).
    opening = f"{topic} — here's what it means and how to actually use it."

    body_bits: list[str] = []
    if key_points:
        body_bits.append(key_points.rstrip(".") + ".")

    spoken = [s for s in _first_sentences(transcript, 4) if len(s) > 25]
    if spoken:
        body_bits.append(" ".join(spoken[:2]))
    elif not key_points:
        body_bits.append(
            "This is one of those AI shifts worth understanding early, "
            "because it changes how fast you can get real work done."
        )

    tags = _pick_hashtags(topic, key_points)

    return (
        f"{opening}\n\n"
        f"{' '.join(body_bits).strip()}\n\n"
        f"{FOLLOW_CTA}\n"
        f"{' '.join(tags)}"
    ).strip()


def build_captions(topic: str, key_points: str = "",
                   transcript: str = "") -> dict:
    """Convenience: return both captions in one call."""
    return {
        "reel_caption": build_reel_caption(topic, key_points, transcript),
        "post_caption": build_post_caption(topic, key_points, transcript),
    }
