# GenZ Capital — Reel Format Spec

The visual/animation language locked in by **Reel #4 (The 4 Secrets)**. Every new
reel should follow this spec unless we're consciously breaking from it. Reference
implementation: [reels/index.html](../reels/index.html) +
[reels/script_reel4.js](../reels/script_reel4.js) + the `.r4-*` rules in
[reels/styles.css](../reels/styles.css).

---

## 1. Core visual language

| Element | Value |
|---|---|
| Canvas | 1080×1920, 30 fps, 30s |
| Background | Pure black `#000` (default for every beat) |
| Primary text | Anton, white `#FFFFFF`, uppercase |
| Punch text | Anton, neon green `#39FF14`, uppercase |
| Subhead text | Inter 700, gray `#A0A0A0`, uppercase, letter-spacing 0.10em |
| Persistent logo | Bottom-right corner, neon divider + "GENZ CAPITAL" Anton text |
| Vignette on screenshots | Radial dark + bottom gradient, brightness 0.62, contrast 1.12 |

The reel is **mostly black-bg with neon green accents**. Screenshots and mock UI
appear sparingly to break up the black. No stock footage, no music — VO + text only.

---

## 2. The 6-beat structure (30s total)

This is the canonical structure. Every reel follows the same beat-count and timing.

| Beat | Time | Purpose | Visual archetype |
|---|---|---|---|
| **B1 HOOK** | 0–4s | Stop the scroll. Promise a payoff. | Flash montage (0–2s) → black + neon punch (2–4s) |
| **B2 POINT #1** | 4–10s | First insight | Black bg + icon + numbered punch |
| **B3 POINT #2** | 10–16s | Second insight | Screenshot tile or visual proof + punch |
| **B4 POINT #3** | 16–22s | Third insight | Mock UI (comment/DM/IG) + punch |
| **B5 POINT #4** | 22–27s | Fourth insight + payoff stamp | Stacked rows + neon stamp |
| **B6 CTA** | 27–30s | Single ask | Black + 3-line CTA (verb / payload / handle) |

**Variations OK** — beat 1 can be all-black if there's no callback to a previous reel; beat 4 can use any mock UI; beat 5 stamp can be replaced by another big-number reveal.

**Variations NOT OK** — never go over 30s. Never put a beat over 7s. Never hold a single static frame longer than 3s.

---

## 3. Timing rules (non-negotiable)

1. **Text reveals 0.4s before its voiceover line.** Eyes register text faster than ears process speech.
2. **First text + first VO syllable both visible by 0.5s.** The thumbnail IS the first frame.
3. **Hard cuts only.** No fades between beats. Crossfades read amateur.
4. **No beat over 7s.** If a point needs more, split it.
5. **VO timing in `LINES_BY_REEL`** in [scripts/generate_reel_voiceover.py](../scripts/generate_reel_voiceover.py) must match GSAP cue starts in `script_reelN.js`. Keep them in sync when editing one.

---

## 4. Animation primitives

These are the GSAP recipes that define the look. Reuse them by name.

### 4a. The "neon punch reveal"

For any neon-green hero text (the `#1 SECRET`, big proof number, CTA mid-line):

```js
tl.fromTo(selector,
  { opacity: 0, scale: 0.3, rotation: -6, filter: 'blur(20px)',
    textShadow: '0 0 0 rgba(57,255,20,0)' },
  { opacity: 1, scale: 1.0, rotation: 0, filter: 'blur(0px)',
    textShadow: '0 0 120px rgba(57,255,20,1), 0 0 240px rgba(57,255,20,0.7)',
    duration: 0.55, ease: 'back.out(2.6)' }, cueTime);
```

### 4b. The "white setup line"

For white preamble text (lines 1 and 3 of a 3-line stack):

```js
tl.fromTo(selector,
  { opacity: 0, y: 60, filter: 'blur(20px)', letterSpacing: '0.20em' },
  { opacity: 1, y: 0, filter: 'blur(0px)', letterSpacing: '0.02em',
    duration: 0.45, ease: 'power3.out' }, cueTime);
```

### 4c. The "sustained neon flicker" (after a reveal)

Drop this 0.3–0.6s after a punch reveal. Repeats give the text a heartbeat.

```js
tl.to(selector, {
  textShadow: '0 0 160px rgba(57,255,20,1), 0 0 320px rgba(57,255,20,0.95)',
  scale: 1.05, duration: 0.30, ease: 'sine.inOut',
  yoyo: true, repeat: 4
}, cueTime + 0.55);
```

### 4d. The "side slide"

For numbered list rows (`#1 / #2 / #3`) — slide from the left, stagger 0.6s apart:

```js
tl.fromTo(selector,
  { opacity: 0, x: -150, filter: 'blur(12px)' },
  { opacity: 1, x: 0, filter: 'blur(0px)',
    duration: 0.45, ease: 'power3.out' }, cueTime);
```

### 4e. The "subhead settle"

For gray Inter subheads — letterspacing tightens as it fades in:

```js
tl.fromTo(selector,
  { opacity: 0, y: 24, letterSpacing: '0.20em' },
  { opacity: 1, y: 0, letterSpacing: '0.10em',
    duration: 0.55, ease: 'power2.out' }, cueTime);
```

### 4f. The "tile fade-in with stagger"

For 2×2 screenshot tiles (Reel 4 beat 3 pattern):

```js
tl.fromTo(selectors,
  { opacity: 0, scale: 0.85, filter: 'blur(15px)' },
  { opacity: 1, scale: 1.0, filter: 'blur(0px)',
    duration: 0.55, ease: 'power3.out',
    stagger: 0.10 }, cueTime);
```

---

## 5. CSS class conventions

Reel-specific classes are namespaced `.r{N}-*`. Reel 4's classes form the canonical
set — copy and rename when you need a new reel:

| Class | Role |
|---|---|
| `.r4-flash` | Full-stage screenshot flash (callback montage) |
| `.r4-hook` / `.r4-hook-num` | Beat 1 black-bg punch container + giant neon number |
| `.r4-secret` | Generic point-card layout (centered column) |
| `.r4-secret-num` | Big neon "#N" header (200px) |
| `.r4-secret-line.a` | White setup line (100px) |
| `.r4-secret-line.b` | Neon punch line (170px) |
| `.r4-secret-sub` | Gray Inter subhead |
| `.r4-tile-grid` | 2×2 image tile (top half of stage) |
| `.r4-comment-mock` / `.r4-dm-mock` | IG comment + DM mockups |
| `.r4-ai-row` | Three-column row: label → arrow → tool |
| `.r4-cta-top` / `.r4-cta-mid` / `.r4-cta-foot` | Beat 6 CTA stack |

Shared (reel-agnostic) classes: `.black-bg`, `.radial-glow`, `.vignette.dark`,
`.logo-corner-persistent`, `.neon`.

---

## 6. Asset patterns

### Callbacks
Every reel should reuse 2–4 assets from a prior reel for ~10% of runtime. Visual
continuity = brand asset. Reel 4 uses Reel 1's profile + post screenshots.

### Mock UI
Build IG-style mocks in pure CSS — never screenshot real Instagram (Originality
Score penalty). The `.r4-comment-mock` and `.r4-dm-mock` classes are reusable.

### No stock footage
Default. If a reel needs B-roll, re-cut the existing `reels/assets/recording.mp4`
into per-scene clips (pitfall: never reference the same video file from multiple
`<video>` tags — see [memory/reels_pipeline.md](../C:/Users/Marc/.claude/projects/c--Users-Marc-Desktop-Gen-Z-autamation/memory/reels_pipeline.md)).

---

## 7. Pitfalls (learned the hard way)

### FP overlap on consecutive clips
Lint error `overlapping_clips_same_track` fires when two clips on the same track
have endpoints that *should* be equal but drift due to floating-point math.

❌ `data-start="0" data-duration="0.55"` followed by `data-start="0.55"` →
   1.10 + 0.55 = 1.6500000000000001 → lint failure.

✅ Use durations exactly representable in binary floating-point: **0.5, 0.25,
   0.125, 1.0, 2.0**. Or stagger by an explicit 0.001s gap.

### Audio not muxed
HyperFrames does NOT mux `<audio data-*>` into the rendered MP4. **Always run
the ffmpeg mux step** after `npm run reels:render`:

```bash
FFMPEG="node_modules/@ffmpeg-installer/win32-x64/ffmpeg.exe"
"$FFMPEG" -y -i renders/reels_<timestamp>.mp4 -i reels/assets/audio/reel<N>_voiceover.mp3 \
  -map 0:v:0 -map 1:a:0 -c:v copy -c:a aac -b:a 192k -shortest \
  renders/reel-<NN>-<slug>.mp4
```

Verify with ffprobe — must show `duration=30.000000`, `1080×1920`, both `h264` and `aac` streams.

---

## 8. How to spawn a new reel from this format

1. **Voiceover lines** — add a new entry to `LINES_BY_REEL` in
   [scripts/generate_reel_voiceover.py](../scripts/generate_reel_voiceover.py).
   6 lines (s1–s6), starting at 0.30, 4.30, 10.30, 16.30, 22.30, 27.30.
2. **Generate VO** — `python scripts/generate_reel_voiceover.py --reel N`
3. **HTML** — copy [reels/index_reel4.html](../reels/index_reel4.html) (after
   archiving) and rename `r4-*` classes to `r{N}-*`. Keep the 6-beat structure.
4. **GSAP** — copy [reels/script_reel4.js](../reels/script_reel4.js), rename to
   `script_reelN.js`, retime cues if you change beat boundaries.
5. **CSS** — duplicate the `/* REEL #4 — THE 4 SECRETS */` section in
   [reels/styles.css](../reels/styles.css) and rename selectors.
6. **Activate** — `node scripts/swap_active_reel.js activate reelN`
7. **Lint** — `npm run reels:lint` (must be 0 errors)
8. **Render** — `npm run reels:render` (~5 min on Windows, 2 workers)
9. **Mux** — ffmpeg command from §7 above
10. **Verify** — ffprobe must show 30.000s + h264 + aac

---

## 9. Smart clip-window selection (tweet-card reels)

For the tweet-card reel format (real footage + static card), the background
clip is a WINDOW cut out of a longer YouTube video. We do **not** take the
first N seconds — that ends the clip on a random timestamp (mid-word, mid-
action, on a smash-cut). Instead we land the cut on a clean boundary, the way
@evolving.ai and pro AI-clipping tools (Opus Clip) do: they detect topic
shifts, sentence boundaries, speech pauses, and visual scene cuts, then end on
a completed thought.

**Module:** [publisher/media_sources/clip_window.py](../publisher/media_sources/clip_window.py)
— free + deterministic (no LLM, no paid API).

Four signals, all from data we already fetch:
1. **Topic payoff** — the transcript cue where the topic keywords / version
   number land densest is the moment the clip must contain.
2. **Sentence / thought boundary** — end at the end of the sentence that
   carries (or immediately follows) the payoff; never mid-word.
3. **Natural pause** — snap the cut to a gap (≥0.45s) between caption cues, so
   the ending feels intentional.
4. **Stable frame** — nudge the end to the nearest ffmpeg scene-cut so we don't
   freeze on a transition frame (one light `select='gt(scene,0.4)'` pass).

Length is **not** fixed: it starts at the best moment and runs until the next
clean boundary, within `[MIN_CLIP 10s, MAX_CLIP 58s]`.

**Timing source:** `transcript_picker.fetch_transcript_cues()` returns the VTT
as timed cues `[{start, end, text}]` (the same free yt-dlp auto-caption fetch
used to pick the video by transcript in §research).

**Wiring:** `tweet_card_reel.build_reel_for_row` calls `choose_clip_window()`
after download, then passes `clip_start`/`clip_end` to `compositor.build()`,
which seeks the source with `-ss <start>` (video+audio in sync) and plays the
window.

**Degrades safely:** a video with no captions → `(0, cap)` "from the start"
(old behavior); any error in windowing → full clip. The build never fails on
clip-window selection.
