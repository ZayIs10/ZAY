# REELS REFERENCE — Gen Z Capital

> **This file is for REELS ONLY.** Carousel posts are a separate pipeline
> (`publisher/carousel_format.py` / `publisher/carousel_generator.py`). When you
> want to review or change anything about how a reel looks or is built, this is
> the one place to look. Every value below is mirrored from the live code — if
> you change a number here, change it in the file noted beside it (and vice
> versa) so this doc never lies.

Last verified against code: **2026-07-19**

---

## 1. What a reel IS (the locked format)

A **tweet-card reel**: real source footage playing in a rectangle, with a fake
"X / Twitter post" card pinned above it. **Only the text is AI-generated** — the
footage is real (a product demo / the actual thing happening). This format is
**LOCKED** — approved 2026-06-03, do not redesign without being asked.

- Canvas: **1080 × 1920** (9:16, vertical).
- **Viral hook opener (added 2026-07-19, user-requested):** every reel OPENS
  with one whole hook clip from **viralhooks.org** (~3–8s of scroll-stopping
  footage — bike crash, watermelon split, …) playing **full-screen and CLEAN
  (no tweet card — user's call, same day)**, then hard-cuts into the normal
  body below, where the card appears.
  Picked deterministically per Topic from the site's ~340-hook library
  (`publisher/hook_opener.py`). Best-effort: if the site is down the reel
  builds without it. Env: `DISABLE_VIRAL_HOOK=1` to turn off,
  `VIRAL_HOOK_SLUG=<name>` to force one.
- Duration: **hook (if any) + 1s poster intro + video, capped at 60s** (the
  body's share shrinks by the hook's length).
- Audio: the hook's own audio during the hook, then the **source clip's own
  audio** (no voiceover/TTS). Silent only if the source has no audio track.
- A reel **MUST use real video**. If no clip can be found/downloaded, the post
  is **SKIPPED** — we never ship a still / Ken Burns image.
- Single clip, **no multi-beat clipping** (multi-beat was tried 2026-06-05 and
  reverted 2026-06-06 — the user disliked the chopped look).

### Not every topic is a reel
Topics are auto-tagged **reel** vs **carousel** before drafting (the
@evolving.ai split). Reels = ONE punchy moment you can feel in 30s with one
visual demo (a launch, a big number). How-tos / "best N tools" / comparisons /
prompt tricks → **carousel**. See `publisher/format_classifier.py`.

---

## 2. The visual layout (geometry)

All coordinates are on the 1080×1920 canvas. Defined in
**`publisher/compositor.py`** (the `CANVAS_*`, `CARD_*`, `VIDEO_*` constants).

```
 (0,0) ┌───────────────────────────────┐ 1080 wide
       │            BLACK               │
       │   ┌───────────────────────┐    │  ← Tweet card PNG
       │   │  ◯ Gen Z Capital ✓    │    │     at (40, 60), 1000×720
       │   │    @genzcapital       │    │
       │   │                       │    │
       │   │  <caption body text>  │    │
       │   │                       │    │
       │   │  We only post the…    │    │  ← signoff line (dim)
       │   └───────────────────────┘    │
       │                               │
       │   ┌───────────────────────┐    │  ← Video rectangle
       │   │                       │    │     at (60, 820), 960×900
       │   │   REAL SOURCE VIDEO   │    │     1s poster image, then
       │   │   (center-cropped)    │    │     the clip plays
       │   │                       │    │
       │   └───────────────────────┘    │
       │            BLACK               │
 (0,1920)└──────────────────────────────┘
```

| Element | Position (x, y) | Size | Code constant |
|---|---|---|---|
| Canvas | (0, 0) | 1080 × 1920 | `CANVAS_W`, `CANVAS_H` |
| Tweet card | (40, 60) | 1000 × 720 | `CARD_X`, `CARD_Y` + `tweet_card.CARD_W/H` |
| Video rect | (60, 820) | 960 × 900 | `VIDEO_X`, `VIDEO_Y`, `VIDEO_W`, `VIDEO_H` |

**Video fill rule:** the clip is **scaled up + center-cropped** to fill the
960×900 rect (`force_original_aspect_ratio=increase` + `crop`). Never
scale-to-fit-with-bars — it must fill the rect. (`compositor.py` `crop_filter`.)

---

## 3. The tweet card — TEXT, FONT & STYLE SPEC

This is the part you'll most often want to tweak. **All of it lives in
`publisher/tweet_card.py`** (constants at the top of the file).

### Card shape & colors
| Thing | Value | Constant |
|---|---|---|
| Card size | 1000 × 720 px | `CARD_W`, `CARD_H` |
| Corner radius | 28 px | `CARD_RADIUS` |
| Card background | `rgba(21,24,28,245)` — near-black, slightly see-through | `CARD_BG` |
| Body / name text | `rgb(231,233,234)` — X dark-mode white | `TEXT_WHITE` |
| Handle / meta / signoff | `rgb(113,118,123)` — X dark-mode gray | `TEXT_DIM` |
| Verified check | `rgb(29,155,240)` — Twitter blue | `CHECK_BLUE` |
| Padding (L/R) | 40 px | `CARD_PAD_X` |
| Padding (top / bottom) | 32 / 28 px | `CARD_PAD_TOP`, `CARD_PAD_BOTTOM` |

> Note the **dark X/Twitter aesthetic** here is intentional and specific to the
> reel card. It is NOT the brand's neon-green cinematic post style (that's for
> static posts / carousels). Don't "fix" the card to neon green.

### Fonts & sizes
| Text role | Font (with fallback) | Size | Constant |
|---|---|---|---|
| Display name ("Gen Z Capital") | **Anton-Regular** → Impact/Arial-Bold | 28 + 4 = **32 px** | `NAME_FONT_SIZE` |
| Handle ("@genzcapital") | **Inter-Regular** → Segoe/Arial | **24 px** | `HANDLE_FONT_SIZE` |
| Body (the caption) | **Inter-Regular** → Segoe/Arial | **30 px** | `BODY_FONT_SIZE` |
| Signoff line | **Inter-Regular** | **22 px** | `SIGNOFF_FONT_SIZE` |
| Body line spacing | +10 px between lines | `BODY_LINE_SPACING` |

Font files live in **`fonts/`** (`Anton-Regular.ttf`, `Inter-Regular.ttf`,
`Inter-Bold.ttf`). If a committed font file is corrupt, `_font()` auto-falls
back to a Windows system font so a render never dies on a bad font.

### Header (avatar + name)
| Thing | Value | Constant |
|---|---|---|
| Avatar | circle, **88 px**, from `logo.png` (white bg auto-removed via alpha bbox) | `AVATAR_SIZE` |
| Gap avatar→name | 16 px | `HANDLE_GAP` |
| Verified check | blue circle + white tick, right of the name | `_draw_check()` |

### Text behavior
- **Body wraps** to the card width word-by-word (`_wrap_to_width`).
- If the caption is **too long**, it's **truncated at a sentence boundary**
  with an ellipsis so the card never overflows (`_truncate_to_fit`).
- **Signoff** is pinned to the bottom of the card. Default text:
  > `"We only post the best AI tools & how-tos"` (`DEFAULT_SIGNOFF`)
- Identity defaults: handle `@genzcapital`, name `Gen Z Capital`
  (set where `render_card(...)` is called in `tweet_card_reel.py`).

---

## 4. The automation pipeline (end to end)

Reels render **in the cloud (GitHub Actions), not the laptop**.

```
Google Sheet "Reels" tab
   │   (a row gets Status = "Ready to Run")
   ▼
n8n Workflow B   ── polls the sheet, claims the row, fires →
   │
   ▼
GitHub Actions:  .github/workflows/build_tweet_card_reel.yml
   │   (also runnable by hand from the Actions UI with a row_index)
   ▼
publisher/tweet_card_reel.py  --row N      ← the orchestrator
   │
   ├─ 1. Read the row (Topic, Post Caption, media URLs)
   ├─ 2. Self-find media if missing  → publisher/media_finder.py
   │       (YouTube Data API search + yt-dlp download + Pexels fallback)
   ├─ 3. No video clip?  → Status = "Skipped - No Video"  (STOP, no still)
   ├─ 4. Download clip + poster       → publisher/media_consumer.py
   ├─ 5. Render the tweet card PNG     → publisher/tweet_card.py
   ├─ 6. Fetch viral hook opener       → publisher/hook_opener.py
   │       (viralhooks.org, free direct MP4; best-effort — reel builds
   │        without it on any failure; no proxy needed)
   ├─ 7. Composite the mp4            → publisher/compositor.py
   │       (hook full-screen first, NO card; then poster + clip with card)
   ├─ 8. Upload to Google Drive        → publisher/publish_reel.py
   └─ 9. Write "Reel MP4 URL" + Status = "Ready to Post" back to the row
```

### Status state machine (prevents duplicate renders)
| Status | Meaning | Set by |
|---|---|---|
| `Ready to Run` | **GO** — user's trigger | user (or research) |
| `Building` | claimed, render in progress | the script (immediately) |
| `Ready to Post` | DONE — mp4 in Drive | the script (success) |
| `Skipped - No Video` | no real clip found, post skipped | the script |
| `Render Failed` | a real error (see `Media Status` cell) | the script |

The trigger word (`Ready to Run`) is deliberately **different** from the done
word (`Ready to Post`) so a re-poll never re-triggers a finished row.

---

## 5. Files map — WHERE TO CHANGE WHAT

| You want to change… | Edit this file |
|---|---|
| Card text, fonts, sizes, colors, signoff | **`publisher/tweet_card.py`** |
| Card / video position & size on the canvas | **`publisher/compositor.py`** (constants) |
| Video crop, audio handling, duration cap, encoding | **`publisher/compositor.py`** (`build()`) |
| Viral hook opener (source site, pick rule, bounds) | **`publisher/hook_opener.py`** |
| Hook full-screen render / concat with the body | **`publisher/compositor.py`** (`_build_hook_segment`) |
| The build order / what counts as "ready", skip rules | **`publisher/tweet_card_reel.py`** |
| How media (clip + poster) is found | `publisher/media_finder.py` + `media_sources/` |
| How clips are downloaded / trimmed | `publisher/media_consumer.py` |
| Reel vs carousel topic decision | `publisher/format_classifier.py` |
| Cloud build (env, secrets, steps) | **`.github/workflows/build_tweet_card_reel.yml`** |
| Drive upload | `publisher/publish_reel.py` |
| Caption/script wording the AI writes | `scripts/research_topic.py` (the GPT prompt) |

### Sheet columns the reel build reads / writes
- **Reads:** `Topic`, `Post Caption`, `Media Video URL`, `Media Image URL`,
  `Key Points`.
- **Writes:** `Status`, `Reel MP4 URL`, `Media Status` (errors),
  `Media Found At`.

---

## 6. Run it yourself (local test)

From the repo root:

```powershell
# Build a specific row locally WITHOUT uploading or touching the sheet:
python publisher/tweet_card_reel.py --row 15 --dry-run

# Build the next "Ready to Run" row for real:
python publisher/tweet_card_reel.py --next

# Just eyeball the tweet card design (no video):
python publisher/tweet_card.py --text "Your caption here"

# Just test the compositor with your own files:
python publisher/compositor.py --card card.png --video clip.mp4 --poster img.jpg --out out.mp4
```

The dry-run writes the mp4 to **`renders/`** and skips Drive + the sheet — use
it to review a design change before shipping.

---

## 7. Quick review checklist

When reviewing a finished reel, check:
- [ ] Opens with a viral hook clip, full-screen and CLEAN — no card until
      the body starts (if the hook is missing entirely, check the Actions
      log for "viral hook:" warnings).
- [ ] Card text is readable, not truncated mid-thought (if it is, shorten the
      caption or raise `max_lines`/card height).
- [ ] Real footage fills the 960×900 rect — no bars, not stretched.
- [ ] Footage is a **demo / the actual thing**, not a talking head.
- [ ] Audio is the clip's own (or intentionally silent), no robotic TTS.
- [ ] Total length ≤ 60s.
- [ ] The topic actually suits a reel (else it should've been a carousel).

---

## 8. Related docs & memory
- `docs/genz_reel_format.md` — the original reel animation/visual language spec.
- `docs/reel_creation.md`, `docs/reels_automation_plan.md` — older planning notes.
- Memory: *Tweet-Card Reel Format LOCKED*, *Reel Status State Machine*,
  *Reel Render Cloud-Only*, *Clips Must Fill Frame*,
  *Visuals Must Match Voiceover*, *Format Qualification*.
```
