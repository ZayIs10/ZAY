"""publisher/media_sources/clip_window.py — pick a clean (start, end) window
inside a long YouTube video so the reel doesn't cut mid-sentence or on a
smash-cut.

The problem
-----------
The reel build used to take the source video FROM THE START, capped at 60s.
That lands the ending on a random timestamp — mid-word, mid-action, on a dead
frame. @evolving.ai and pro AI-clipping tools (Opus Clip et al.) instead end on
a COMPLETED thought: they detect topic shifts, sentence boundaries, speech
pauses, and visual scene cuts, then land the cut on the nearest clean boundary.

We replicate the free, deterministic core of that using signals we already have:
  1. TOPIC PAYOFF  — the transcript cue where the topic keywords/version land
     densest is the moment the clip must contain.
  2. SENTENCE / THOUGHT boundary — end at the end of the sentence that carries
     (or immediately follows) the payoff, never mid-word.
  3. NATURAL PAUSE — snap the cut to a gap between caption cues (a speech pause),
     so the ending feels intentional, not chopped.
  4. STABLE FRAME  — nudge the end to the nearest ffmpeg-detected scene cut so we
     don't freeze on a smash-cut / transition frame.

The length is NOT fixed — we start at the best moment and run until the next
clean boundary lands, within [MIN_CLIP, MAX_CLIP]. No LLM, no paid API.

All timing signals come from the timestamped transcript (transcript_picker.
fetch_transcript_cues) plus one light ffmpeg scene-detect pass. Every step
degrades safely: missing captions -> fall back to "from the start, capped".
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

log = logging.getLogger("media_sources.clip_window")

# reuse the one keyword/version definition the whole pipeline shares — robust
# across every import style (see transcript_picker for the same pattern).
try:
    from publisher.media_sources.scoring import topic_keywords
except Exception:  # noqa: BLE001
    try:
        from media_sources.scoring import topic_keywords  # type: ignore
    except Exception:  # noqa: BLE001
        try:
            from scoring import topic_keywords  # type: ignore
        except Exception:  # noqa: BLE001
            import sys as _sys
            _sys.path.insert(0, str(Path(__file__).resolve().parent))
            from scoring import topic_keywords  # type: ignore


# --- tunables --------------------------------------------------------------
MIN_CLIP = 10.0        # never ship a clip shorter than this
TARGET_CLIP = 30.0     # sweet-spot length — once past this, the first strong
                       # ending wins (don't stretch to the cap for its own sake)
MAX_CLIP = 58.0        # under the 60s IG Reels ceiling, leaves compositor room
LEAD_IN = 1.2          # start this many seconds before the payoff sentence...
LEAD_IN_MAX_BACK = 4.0 # ...but never rewind more than this to a sentence start
PAUSE_GAP = 0.45       # a gap >= this between cues counts as a speech pause
SCENE_SNAP_WINDOW = 2.5  # only snap the end to a scene cut within this window
SENTENCE_END_RE = re.compile(r"[.!?]['\")\]]?\s*$")
# Function/filler words that make a BAD cut point — a clip ending on "and",
# "the", "that's", "to"... feels chopped. Used only in the last-resort fallback
# when there's no punctuation and no speech-gap to cut on (gapless captions).
_DANGLING_END_RE = re.compile(
    r"\b(a|an|the|and|or|but|so|to|of|in|on|at|for|with|is|are|was|were|"
    r"that|that's|this|these|those|it's|i|we|you|they|he|she|my|your|our|"
    r"as|if|because|when|while|which|who|what|how|uh|um|like|really)\s*$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Transcript-driven boundary logic
# ---------------------------------------------------------------------------

def _payoff_cue_index(cues: list[dict], topic: str, context: str = "") -> int:
    """Index of the cue that best represents the topic payoff — the one whose
    text carries the most distinctive topic terms (version numbers weigh most).
    Falls back to the FIRST cue when the topic words never appear."""
    words, versions = topic_keywords(topic)
    if context:
        ctx_words, _ = topic_keywords(context)
        words = words | ctx_words
    if not words and not versions:
        return 0

    best_i, best_score = 0, -1
    for i, cue in enumerate(cues):
        text_l = cue["text"].lower()
        score = sum(2 for w in words if w in text_l)
        score += sum(6 for v in versions
                     if re.search(rf"(?<![\w.]){re.escape(v)}(?![\w.])", text_l))
        if score > best_score:
            best_i, best_score = i, score
    # No cue mentioned the topic at all -> no meaningful payoff; start at 0.
    return best_i if best_score > 0 else 0


def _is_sentence_end(text: str) -> bool:
    return bool(SENTENCE_END_RE.search(text.strip()))


def _pause_after(cues: list[dict], i: int) -> bool:
    """True if there's a speech pause (caption gap) right after cue i."""
    if i + 1 >= len(cues):
        return True  # end of transcript = the ultimate pause
    return (cues[i + 1]["start"] - cues[i]["end"]) >= PAUSE_GAP


def choose_window_from_cues(
    cues: list[dict], topic: str, context: str = "",
    *, min_clip: float = MIN_CLIP, max_clip: float = MAX_CLIP,
) -> tuple[float, float, dict]:
    """Pick (start, end) purely from the timestamped transcript.

    Strategy:
      - Anchor on the payoff cue.
      - START: back up to the start of the sentence containing the payoff (so
        the clip has a bit of run-up), snapped to a preceding pause, but no more
        than LEAD_IN_MAX_BACK seconds before the payoff.
      - END: walk forward from the payoff to the first cue that BOTH ends a
        sentence AND is followed by a pause -> the clean ending. Clamp to
        [min_clip, max_clip].
    Returns (start, end, detail).
    """
    if not cues:
        return 0.0, max_clip, {"reason": "no cues"}

    p = _payoff_cue_index(cues, topic, context)
    payoff_start = cues[p]["start"]

    # --- START: rewind to the sentence start, snapped to a pause -------------
    start_i = p
    while start_i > 0:
        prev = cues[start_i - 1]
        # Stop if the previous cue already ended a sentence (this cue starts a
        # new one) or there's a pause before this cue, or we've rewound enough.
        if _is_sentence_end(prev["text"]):
            break
        if (cues[start_i]["start"] - prev["end"]) >= PAUSE_GAP:
            break
        if payoff_start - prev["start"] > LEAD_IN_MAX_BACK:
            break
        start_i -= 1
    start = max(0.0, min(cues[start_i]["start"], payoff_start - 0.0))
    # A touch of lead-in so we don't start on the very first phoneme.
    start = max(0.0, start - 0.0)  # sentence start already gives run-up

    # --- END: cleanest boundary at/after the payoff -------------------------
    # Real YouTube auto-captions almost NEVER carry sentence punctuation (they
    # are rolling captions — periods appear <1% of the time). So we can't rely
    # on `.!?` alone. We rank candidate endings by how clean they are and take
    # the best one inside [min_clip, max_clip]:
    #   3 = sentence punctuation AND a following speech pause  (ideal)
    #   2 = sentence punctuation only
    #   1 = a clear speech pause only  (a real gap = a natural thought break —
    #       this is what carries auto-captioned videos)
    # Among same-quality endings, prefer the LONGEST clip in range (lets a
    # thought complete) rather than the first one just past min_clip.
    def _end_quality(i: int) -> int:
        sent = _is_sentence_end(cues[i]["text"])
        pause = _pause_after(cues, i)
        if sent and pause:
            return 3
        if sent:
            return 2
        if pause:
            return 1
        return 0

    def _pause_size(i: int) -> float:
        if i + 1 >= len(cues):
            return 99.0  # end of transcript = the biggest pause of all
        return max(0.0, cues[i + 1]["start"] - cues[i]["end"])

    # Score every in-range cue and keep the best ending. Preference order:
    #   1. higher quality (sentence+pause > sentence > pause)
    #   2. once we've passed TARGET_CLIP, a bigger pause = a cleaner breath
    #   3. reaching at least the target length (so we don't cut a 12s clip when
    #      a clean 30s one exists just ahead)
    best_i, best_key = None, None
    for i in range(p, len(cues)):
        elapsed = cues[i]["end"] - start
        if elapsed < min_clip:
            continue  # too short — keep going to reach a satisfying length
        if elapsed > max_clip:
            break
        q = _end_quality(i)
        if q < 1:
            continue  # not a boundary at all — never end here
        reached_target = elapsed >= min(TARGET_CLIP, max_clip)
        # Sort key (higher is better): prefer quality, then having reached the
        # sweet-spot length, then a longer/cleaner pause.
        key = (q, 1 if reached_target else 0, round(_pause_size(i), 2), elapsed)
        if best_key is None or key > best_key:
            best_i, best_key = i, key

    if best_i is not None:
        end = cues[best_i]["end"]
        reason = {3: "sentence+pause", 2: "sentence end",
                  1: "speech pause"}[_end_quality(best_i)]
    else:
        # Nothing clean in range (dense, gapless speech — common when auto-
        # caption timings are interpolated, so there are no real gaps and no
        # punctuation). Don't stretch to the 58s cap for nothing: land near the
        # TARGET length, on the cue that best completes a phrase (ends on a
        # content word, not a dangling "and / that's / the / to"). We still cut
        # BETWEEN cues, never inside a caption line.
        target_end = start + min(TARGET_CLIP, max_clip)
        candidates = []
        for i in range(p, len(cues)):
            e_i = cues[i]["end"]
            if e_i - start < min_clip:
                continue
            if e_i - start > max_clip:
                break
            # distance from the sweet spot, and whether it dangles on a
            # function word (bad place to cut).
            dangling = bool(_DANGLING_END_RE.search(cues[i]["text"]))
            candidates.append((abs(e_i - target_end), dangling, e_i))
        if candidates:
            # prefer non-dangling, then closest to target length
            candidates.sort(key=lambda c: (c[1], c[0]))
            end = candidates[0][2]
            reason = ("phrase boundary near target"
                      if not candidates[0][1] else "cue boundary near target")
        else:
            end = min(start + max_clip, cues[-1]["end"])
            reason = "hard cap (no boundary in range)"

    # Guarantee minimum length even if the payoff sits near the end.
    if end - start < min_clip:
        end = min(start + min_clip, cues[-1]["end"] if cues else start + min_clip)

    detail = {
        "payoff_cue": p,
        "payoff_start": round(payoff_start, 2),
        "payoff_text": cues[p]["text"][:80],
        "start_cue": start_i,
        "end_quality": _end_quality(best_i) if best_i is not None else 0,
        "reason": reason,
    }
    return round(start, 2), round(end, 2), detail


# ---------------------------------------------------------------------------
# Visual scene-cut snapping (avoid ending on a smash-cut)
# ---------------------------------------------------------------------------

def detect_scene_cuts(video_path: Path, ffmpeg: str = "ffmpeg",
                      threshold: float = 0.4) -> list[float]:
    """Return timestamps (seconds) of visual scene cuts in `video_path` via
    ffmpeg's scene-change detector. Empty list on any failure (scene snapping
    is best-effort — a missing list just means we keep the transcript cut)."""
    cmd = [
        ffmpeg, "-hide_banner", "-i", str(video_path),
        "-filter_complex", f"select='gt(scene,{threshold})',metadata=print",
        "-an", "-f", "null", "-",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except (subprocess.SubprocessError, OSError) as exc:
        log.info("Scene detect failed (%s) — skipping frame snap.", exc)
        return []
    times: list[float] = []
    # ffmpeg prints "pts_time:12.345" lines for each detected scene change.
    for m in re.finditer(r"pts_time:([0-9.]+)", proc.stderr or ""):
        try:
            times.append(float(m.group(1)))
        except ValueError:
            continue
    return sorted(times)


def snap_end_to_scene(end: float, scene_cuts: list[float],
                     *, window: float = SCENE_SNAP_WINDOW,
                     start: float = 0.0, min_clip: float = MIN_CLIP) -> float:
    """If a scene cut lands within `window` seconds BEFORE the transcript end,
    pull the end back to just before it — so the clip ends on the last stable
    frame of the current shot, not the first frame of the next one. Never
    shortens below min_clip. Returns the (possibly unchanged) end."""
    if not scene_cuts:
        return end
    best = None
    for t in scene_cuts:
        # A cut slightly before our end, but not so early it kills the clip.
        if end - window <= t <= end and (t - start) >= min_clip:
            best = t
    if best is None:
        return end
    # Land ~0.1s before the scene change so we hold the last stable frame.
    return round(max(start + min_clip, best - 0.1), 2)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def choose_clip_window(
    topic: str,
    *,
    cues: list[dict] | None = None,
    video_url: str | None = None,
    video_path: Path | None = None,
    context: str = "",
    ffmpeg: str = "ffmpeg",
    min_clip: float = MIN_CLIP,
    max_clip: float = MAX_CLIP,
) -> dict:
    """Pick a clean (start, end) window for the reel clip.

    Provide `cues` directly, or a `video_url` to fetch them. Optionally pass the
    already-downloaded `video_path` to enable scene-cut snapping on the ending.

    Returns:
        {"start": float, "end": float, "duration": float,
         "snapped_to_scene": bool, "detail": {...}}

    Degrades safely: no captions -> (0, max_clip) "from the start" so the build
    never fails just because a video lacks a transcript.
    """
    if cues is None and video_url:
        try:
            from publisher.media_sources.transcript_picker import (
                fetch_transcript_cues,
            )
        except Exception:  # noqa: BLE001
            try:
                from media_sources.transcript_picker import (  # type: ignore
                    fetch_transcript_cues,
                )
            except Exception:  # noqa: BLE001
                import sys as _sys
                _sys.path.insert(0, str(Path(__file__).resolve().parent))
                from transcript_picker import (  # type: ignore
                    fetch_transcript_cues,
                )
        cues = fetch_transcript_cues(video_url)

    if not cues:
        log.info("No transcript cues — clip from start, capped at %.0fs.",
                 max_clip)
        return {
            "start": 0.0, "end": max_clip, "duration": max_clip,
            "snapped_to_scene": False,
            "detail": {"reason": "no transcript; from start"},
        }

    start, end, detail = choose_window_from_cues(
        cues, topic, context, min_clip=min_clip, max_clip=max_clip,
    )

    snapped = False
    if video_path and Path(video_path).exists():
        cuts = detect_scene_cuts(Path(video_path), ffmpeg=ffmpeg)
        new_end = snap_end_to_scene(end, cuts, start=start, min_clip=min_clip)
        if abs(new_end - end) > 0.05:
            log.info("Snapped end %.2fs -> %.2fs (scene cut).", end, new_end)
            end = new_end
            snapped = True

    return {
        "start": round(start, 2),
        "end": round(end, 2),
        "duration": round(end - start, 2),
        "snapped_to_scene": snapped,
        "detail": detail,
    }
