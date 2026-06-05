"""Split a reel caption into ordered 'beats' for the multi-beat reel format.

A beat = one on-screen text line that gets its own background clip and its
own reveal, cut in sequence (the @evolving.ai look). The caption stored in
the Reels sheet is already newline-separated, one idea per line, with a
trailing hashtag line — so splitting is mostly: take the lines, drop the
hashtags, drop empties, cap the count.

Kept dependency-free and pure so it's trivially testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Reels run ~3-4s per beat; 3-5 beats keeps the whole thing in the
# 12-25s sweet spot that holds retention without dragging.
MIN_BEATS = 3
MAX_BEATS = 5

# Per-beat on-screen text must fit the 1080px frame (see
# feedback_reel_text memory) — keep each beat short. Longer lines are
# allowed through but flagged so the card renderer can wrap/shrink.
_HASHTAG_LINE = re.compile(r"^\s*#\S+(\s+#\S+)*\s*$")


@dataclass
class Beat:
    index: int          # 0-based order
    text: str           # the on-screen line (also the search seed)
    is_cta: bool        # last "Follow for..." style line, if detected


def _looks_like_cta(line: str) -> bool:
    low = line.lower()
    return any(
        kw in low for kw in (
            "follow for", "follow us", "follow @", "drop a", "comment",
            "save this", "share this", "link in bio",
        )
    )


def split_caption(caption: str) -> list[Beat]:
    """Split `caption` into ordered Beats.

    - Splits on newlines first (the sheet's native structure).
    - Drops the hashtag line and any empty lines.
    - If there's only ONE line (no newlines), falls back to splitting on
      sentence boundaries so we still get multiple beats.
    - Caps at MAX_BEATS, keeping the first beats (the hook matters most);
      a trailing CTA line is preserved as the final beat when present.
    """
    if not caption or not caption.strip():
        return []

    raw_lines = [ln.strip() for ln in caption.splitlines()]
    lines = [
        ln for ln in raw_lines
        if ln and not _HASHTAG_LINE.match(ln)
    ]

    # Single-line caption -> split into sentences so we still get beats.
    if len(lines) <= 1:
        text = lines[0] if lines else caption.strip()
        parts = re.split(r"(?<=[.!?])\s+", text)
        lines = [p.strip() for p in parts if p.strip()]

    if not lines:
        return []

    # Separate a trailing CTA so we can always keep it as the last beat.
    cta_line = None
    if _looks_like_cta(lines[-1]):
        cta_line = lines[-1]
        lines = lines[:-1]

    # Cap the content beats, leaving room for the CTA if present.
    budget = MAX_BEATS - (1 if cta_line else 0)
    if len(lines) > budget:
        lines = lines[:budget]

    if cta_line:
        lines.append(cta_line)

    beats: list[Beat] = []
    for i, ln in enumerate(lines):
        beats.append(Beat(
            index=i,
            text=ln,
            is_cta=(cta_line is not None and ln == cta_line),
        ))
    return beats


if __name__ == "__main__":  # quick manual check
    sample = (
        "The Gates Foundation just put 200 million dollars into Anthropic.\n"
        "The mission is AI for global health.\n"
        "Bill Gates picked one model to bet on - and it wasn't OpenAI.\n"
        "When the biggest names move, pay attention to who they pick.\n"
        "Follow for the next Anthropic drop.\n\n"
        "#anthropic #claude #ai"
    )
    for b in split_caption(sample):
        tag = " [CTA]" if b.is_cta else ""
        print(f"{b.index}: {b.text}{tag}")
