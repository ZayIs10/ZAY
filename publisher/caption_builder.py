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

# Engagement hook — the FIRST line of every post caption. Drives the "comment
# a word -> auto-DM" loop (the highest-reach IG growth play). Constant, brand-
# approved copy (user locked this format 2026-07-04).
ENGAGEMENT_HOOK = 'Like this post + Comment "Send" and I\'ll DM you the link.'

# Follow CTA — brand rule: name the value, never bare "follow for more".
FOLLOW_CTA = (
    "Follow @genzcapital, I find the AI tools worth your time so you don't "
    "have to."
)

# Hashtag pool. Lowercase (the locked format uses #ai #chatgpt, not #AI). #ai +
# #aitools + #tech are brand-constant and always present; the rest are added
# only when the topic mentions them, so IG never sees a spammy >6 block.
BASE_TAGS = ["#ai", "#aitools"]
CLOSING_TAG = "#tech"  # always the final tag (matches the locked example)
TAG_TRIGGERS = [
    (("chatgpt", "gpt", "openai"), "#chatgpt"),
    (("claude", "anthropic"), "#claude"),
    (("prompt",), "#prompting"),
    (("browser", "aside", "comet"), "#aibrowser"),
    (("agent", "agentic"), "#aiagent"),
    (("automate", "automation", "workflow"), "#automation"),
    (("code", "coding", "developer", "app"), "#coding"),
    (("productiv", "faster", "hours", "save"), "#productivity"),
]


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _first_sentences(text: str, n: int) -> list[str]:
    """Split into sentence-ish chunks and return the first n non-empty ones."""
    parts = re.split(r"(?<=[.!?])\s+", _clean(text))
    return [p.strip() for p in parts if p.strip()][:n]


def _pick_hashtags(topic: str, key_points: str) -> list[str]:
    """Return 4-6 lowercase tags: #ai #aitools first, topic-matched middle,
    #tech last — matching the locked example (#ai #chatgpt #claude ... #tech)."""
    blob = f"{topic} {key_points}".lower()
    tags = list(BASE_TAGS)
    for needles, tag in TAG_TRIGGERS:
        if any(nd in blob for nd in needles) and tag not in tags:
            tags.append(tag)
        if len(tags) >= 5:  # leave room for the constant closing tag
            break
    if CLOSING_TAG not in tags:
        tags.append(CLOSING_TAG)
    return tags


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
    """The IG text-box caption in the LOCKED format (user set 2026-07-04).

    Every caption follows this exact block structure so the whole feed reads
    consistently and runs the comment->auto-DM growth loop:

        1. Engagement hook  (constant: "Like this post + Comment 'Send'...")
        2. Keyword opener   (topic-first line, SEO signal)
        3. Value body       (what it breaks down / why it matters)
        4. Who it's for     ("Perfect for anyone...")
        5. Punchy takeaway  (one memorable line)
        6. Follow CTA       (constant, value-named)
        7. Hashtags         (lowercase, 4-6, #ai... #tech)

    Blocks are separated by a blank line (IG renders these as paragraph gaps),
    except the hashtags sit directly under the follow CTA. No emojis. Body is
    grounded in Key Points + the video transcript when present so it stays
    specific per post instead of boilerplate.
    """
    topic = _clean(topic).rstrip(".")
    key_points = _clean(key_points)

    # 2. Keyword opener — MUST carry the topic keyword (search + relevance).
    # If the topic is already a full statement (has a colon or is a long
    # phrase), let it stand as the opener; otherwise add a hook clause. This
    # avoids "...Worth Paying For: here's what's actually worth knowing."
    if ":" in topic or len(topic.split()) >= 7:
        opener = topic + "."
    else:
        opener = f"{topic}: here's what's actually worth knowing."

    # 3. Value body. Prefer Key Points; enrich with the first real spoken
    # sentences from the video so the copy describes THIS video, not a template.
    spoken = [s for s in _first_sentences(transcript, 4) if len(s) > 25]
    body_parts: list[str] = []
    if key_points:
        body_parts.append(key_points.rstrip(".") + ".")
    if spoken:
        body_parts.append(" ".join(spoken[:2]))
    if not body_parts:
        body_parts.append(
            "This breaks down exactly how it works and where it actually "
            "saves you time, without the hype."
        )
    body = " ".join(body_parts).strip()

    # 4. Who it's for. Anchored to a short subject phrase, not the whole title
    # jammed in lowercase. Take the part of the topic before a colon (the
    # subject) and keep it under ~5 words so the line reads naturally.
    subject = topic.split(":", 1)[0].strip()
    if len(subject.split()) > 5:
        subject = ""  # too long to inline cleanly — use a generic close
    if subject:
        who = f"Perfect for anyone weighing up {subject.lower()}."
    else:
        who = "Perfect for anyone trying to actually get value out of AI, fast."

    # 5. Punchy takeaway — one memorable, opinionated line.
    takeaway = "Don't adopt AI out of FOMO. Use it where it removes real work."

    tags = _pick_hashtags(topic, key_points)

    return (
        f"{ENGAGEMENT_HOOK}\n\n"
        f"{opener}\n\n"
        f"{body}\n\n"
        f"{who}\n\n"
        f"{takeaway}\n\n"
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


# ---------------------------------------------------------------------------
# Motivation-channel captions (user, 2026-09-04): the comment-"Send" DM hook
# and the AI-tool voice belong to the GenZ Capital AI content ONLY. Motivation
# reels get their own voice — a quote from the speech, no DM loop, motivation
# hashtags.
# ---------------------------------------------------------------------------

MOTIVATION_FOLLOW_CTA = (
    "Follow @genzcapital for daily motivation that actually hits."
)
MOTIVATION_TAGS = "#motivation #mindset #discipline #success #dailymotivation"


def _tidy_spoken(s: str) -> str:
    """Clean a transcript sentence for print: collapse spoken stutters
    ("he would he would" -> "he would") and capitalize the first letter."""
    s = re.sub(r"\b(\w+)(?: \1\b)+", r"\1", s, flags=re.IGNORECASE)
    s = re.sub(r"\b(\w+ \w+)(?: \1\b)+", r"\1", s, flags=re.IGNORECASE)
    s = s.strip()
    return (s[:1].upper() + s[1:]) if s else s


def _power_words() -> frozenset:
    try:
        from publisher.speech_captions import POWER_WORDS
        return POWER_WORDS
    except Exception:  # noqa: BLE001 — scoring is a bonus, not a dependency
        return frozenset()


def _continuation_words() -> frozenset:
    try:
        from publisher.speech_captions import _CONTINUATION_WORDS
        return _CONTINUATION_WORDS
    except Exception:  # noqa: BLE001
        return frozenset()


def _best_quote(spoken: list[str]) -> str:
    """The most charged printable line: quote-length sentences ranked by how
    many motivation power words they carry (speech_captions.POWER_WORDS —
    same list that picks the neon caption words), first-wins on ties."""
    power = _power_words()
    best, best_score = "", -1
    for s in spoken:
        if not 30 <= len(s) <= 140:
            continue
        toks = re.findall(r"[a-z']+", s.lower())
        score = sum(t in power for t in toks)
        if score > best_score:
            best, best_score = s, score
    return best


def _quote_from_flat(text: str) -> str:
    """Quote picker for UNPUNCTUATED auto-caption transcripts (one giant
    "sentence"): slide 10/14-word windows, keep the one densest in power
    words, then trim dangling continuation words off both edges so the quote
    starts and ends on words that can carry a thought."""
    power, cont = _power_words(), _continuation_words()
    toks = _clean(text).split()
    if len(toks) < 8:
        return ""

    def norm(w: str) -> str:
        return re.sub(r"[^a-z0-9]", "", w.lower())

    best_i, best_len, best_score = 0, min(14, len(toks)), -1
    for i in range(len(toks) - 7):
        for length in (10, 14):
            if i + length > len(toks):
                continue
            score = sum(norm(w) in power for w in toks[i:i + length])
            if score > best_score:
                best_i, best_len, best_score = i, length, score
    win = toks[best_i:best_i + best_len]
    for _ in range(3):
        if win and norm(win[0]) in cont:
            win.pop(0)
    for _ in range(3):
        if win and norm(win[-1]) in cont:
            win.pop()
    return " ".join(win)


def build_motivation_captions(topic: str, key_points: str = "",
                              transcript: str = "") -> dict:
    """Post + reel captions for a motivation-speech reel.

    Structure: quote hook (a real line from the speech when captions give us
    one) -> the speech name -> a rewatch line -> follow CTA + constant
    motivation tags. Deterministic and free, like build_captions."""
    topic = _clean(topic)

    spoken = [s for s in _first_sentences(transcript, 14) if len(s) > 30]
    if len(spoken) >= 3:
        quote = _best_quote(spoken)
    else:
        # Auto-captions often carry no punctuation — one giant "sentence".
        quote = _quote_from_flat(transcript)
    quote = _tidy_spoken(quote).rstrip(".!?").rstrip(" ,;:")
    hook = f'"{quote}."' if quote else "Save this for the day you need it."

    parts = [hook, topic.rstrip(".") + "."]
    parts.append("Watch this again on the days you feel like quitting.")
    parts.append(f"{MOTIVATION_FOLLOW_CTA}\n{MOTIVATION_TAGS}")
    post = "\n\n".join(parts)

    reel = topic.rstrip(".") + "."
    if quote:
        reel += f"\n{hook}"
    return {"reel_caption": reel, "post_caption": post}
