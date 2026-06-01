# Reels Automation — Source of Truth Plan

> **Rule:** This document is the canonical description of how the reels pipeline runs.
> When something needs to change, **edit this file first**, then update the code to match.
> If reality and this doc disagree, this doc is wrong — fix the doc, then fix the code.

---

## Big picture

```
You (terminal)                                You (Google Sheets)
      │                                              │
      │  python scripts/research_topic.py "<topic>"  │
      ▼                                              │
┌─────────────────────┐                              │
│ 1. Research + draft │                              │
│    DuckDuckGo + YT  │                              │
│    GPT-4o (json)    │                              │
└──────────┬──────────┘                              │
           │  appends row, Status = Draft            │
           ▼                                         ▼
┌────────────────────────────────────────────────────────────┐
│  Google Sheet "Reels" tab — row N (Status = Draft)         │
│  You review, edit headlines/script/voiceover, flip         │
│  Status → Ready                                            │
└────────────────────────────┬───────────────────────────────┘
                             │
      python scripts/build_and_publish_reel.py [--row N]
                             ▼
        ┌────────────────────────────────────────┐
        │ 2. Voiceover (OpenAI TTS, onyx)        │
        │ 3. Render reel (HyperFrames, silent)   │
        │ 4. Mux audio (ffmpeg)                  │
        │ 5. Verify (ffprobe)                    │
        │ 6. Publish (Drive → Instagram Graph)   │
        │ 7. Mark row Published + permalink      │
        └────────────────────────────────────────┘
                             │
                             ▼
                     Posted on Instagram
```

---

## Stage 1 — Research (CLI → Sheet Draft)

**Command**
```powershell
python scripts/research_topic.py "<your topic>"
```

**Script:** [scripts/research_topic.py](../scripts/research_topic.py)

**What it does**
1. Loads `.env` and `research/research_config.json`.
2. Enriches the topic with free sources:
   - DuckDuckGo News (top 5 stories)
   - YouTube Data API (top 3 videos → first one populates `YouTube URL`)
3. Calls **GPT-4o** (`response_format=json_object`) once. Model returns a single JSON object containing:
   - `template` — `montage_hook` or `five_beat` (model picks based on topic)
   - `post_caption` — 150–200 word IG caption with hashtags
   - `headline_line_1/2/3`, `subheadline`
   - `reel_script` — 5 punchy on-screen lines, newline-separated
   - `proof_number`, `proof_label`
   - `voiceover_lines` — list of `{id, start, text}` aligned to a 30s timeline
4. Calls `SheetsReader.ensure_columns()` to add any missing column headers (idempotent).
5. Appends one new row to the **Reels** tab with `Status=Draft`.
6. Prints the row index and a deep-link URL into the Sheet.

**Sheet target:** the tab named by `$env:GOOGLE_SHEET_REELS_NAME` (default `"Reels"`).

---

## Stage 2 — Manual approval (you, in Sheets)

1. Open the printed URL.
2. Review and edit any column. The most worth-checking ones:
   - `Reel Template` (override the model's choice if you disagree)
   - `Headline Line 1/2/3 (...)`, `Subheadline (Gray)`, `Key Stat`
   - `Reel Script` (5 lines)
   - `Voiceover Lines (JSON)` — the timing is what the renderer aligns to
3. **Flip `Status` from `Draft` → `Ready`.**

The build script picks up the next `Ready` row, or a specific row if you pass `--row N`.

---

## Stage 3 — Build + publish

**Command**
```powershell
python scripts/build_and_publish_reel.py            # next Ready row
python scripts/build_and_publish_reel.py --row 12   # specific row
python scripts/build_and_publish_reel.py --no-publish   # build local MP4 only
python scripts/build_and_publish_reel.py --no-mux       # skip audio mux (debug)
```

**Script:** [scripts/build_and_publish_reel.py](../scripts/build_and_publish_reel.py)

**Substeps**

| # | Action | Tool / file | Output |
|---|---|---|---|
| 1 | Find row | `reel_generator.load_sheet_row()` | row dict + reader |
| 2 | Build voiceover | subprocess → [scripts/generate_reel_voiceover.py](../scripts/generate_reel_voiceover.py) `--from-sheet <row>` | `reels/assets/audio/<slug>_voiceover.mp3` (~30s) |
| 3 | Render reel | subprocess → [publisher/reel_generator.py](../publisher/reel_generator.py) `--from-sheet <row>` | silent MP4 at `assets/reels/<slug>.mp4` (or `renders/reels_*.mp4`) |
| 4 | Mux audio | `ffmpeg -map 0:v:0 -map 1:a:0 -c:v copy -c:a aac` | `renders/<slug>.mp4` (with audio) |
| 5 | Verify | `ffprobe` | duration ≈ 30s, 1080×1920, h264 + aac |
| 6 | Publish | subprocess → [publisher/publish_reel.py](../publisher/publish_reel.py) | Drive upload → IG Graph API → permalink + media_id |
| 7 | Mark row | `SheetsReader.mark_published()` | `Status=Published`, `Post URL`, `Instagram Post ID` |

`--no-publish` stops after step 5 — useful for previewing before any IG API calls.

---

## Reels Sheet schema

The "Reels" tab is set up by [scripts/setup_reels_sheet.py](../scripts/setup_reels_sheet.py) (idempotent — safe to re-run).

| # | Column | Set by | Purpose |
|---|---|---|---|
| 1 | Topic | research | the input topic |
| 2 | Key Points | research | 3–5 bullet takeaways |
| 3 | Brand Tone | config | from `research_config.json` |
| 4 | Enriched Context | research | DuckDuckGo + YT digest |
| 5 | YouTube URL | research | top YT result for B-roll reference |
| 6 | Status | you / publisher | `Draft` → `Ready` → `Published` |
| 7 | Published Date | publisher | ISO timestamp on success |
| 8 | Post URL | publisher | IG permalink |
| 9 | Instagram Post ID | publisher | IG media id |
| 10 | Image | (unused for reels) | reserved |
| 11 | Post Type | research | always `reel` |
| 12 | Slide Content | (unused for reels) | reserved |
| 13 | Headline Line 1 (White) | research | scene-1 top line |
| 14 | Headline Line 2 (Neon Green) | research | scene-1 hero number/word |
| 15 | Headline Line 3 (White) | research | scene-1 bottom line |
| 16 | Subheadline (Gray) | research | scene-1 subhead |
| 17 | Key Stat | research | proof number, e.g. `47%`, `$1.4M` |
| 18 | Reel Script | research | 5 newline-separated on-screen lines |
| 19 | Reel Template | research | `montage_hook` \| `five_beat` |
| 20 | Voiceover Lines (JSON) | research | `[{"id","start","text"}, ...]` aligned to 30s |
| 21 | Reel MP4 URL | publisher (optional) | Drive URL for re-posting |

---

## Reel templates

### `montage_hook`
Clone of [reels/archive/index_reel1.html](../reels/archive/index_reel1.html) — 6 profile screenshots → question → reveal → cliffhanger → CTA. Uses the **fixed** brand-screenshot set in `reels/assets/account_screenshots/` (those 6 images are social proof and stay identical across topics). Best for "look at these accounts/examples" angles.

The renderer (`build_montage_hook_html`) loads the archived HTML as a template and string-replaces only:
- Scene 3 reveal lines
- Scene 4 cliffhanger ("4 MORE TRICKS")
- Scene 5 CTA word ("NEXT")
- Audio src

### `five_beat`
Generic 30s reel: Hook → Problem → Insight → Proof → CTA. Built dynamically from the Sheet row (headlines + script + key stat).

**Selection:** GPT-4o picks during research. You can override in column 19 before flipping `Status=Ready`.

---

## Files reference

| File | Role |
|---|---|
| [scripts/research_topic.py](../scripts/research_topic.py) | Topic → enriched draft → Sheet row |
| [scripts/build_and_publish_reel.py](../scripts/build_and_publish_reel.py) | Orchestrator: voiceover → render → mux → publish |
| [scripts/generate_reel_voiceover.py](../scripts/generate_reel_voiceover.py) | OpenAI TTS → 30s aligned MP3 |
| [scripts/setup_reels_sheet.py](../scripts/setup_reels_sheet.py) | One-time Sheet tab + headers setup |
| [publisher/reel_generator.py](../publisher/reel_generator.py) | HyperFrames render dispatcher (both templates) |
| [publisher/publish_reel.py](../publisher/publish_reel.py) | Drive upload + Instagram Graph publish |
| [publisher/post_generator.py](../publisher/post_generator.py) | `SheetsReader` (reused by the reels stack) |
| [reels/archive/index_reel1.html](../reels/archive/index_reel1.html) | Read-only template for `montage_hook` |
| [reels/archive/script_reel1.js](../reels/archive/script_reel1.js) | Read-only GSAP timeline for `montage_hook` |
| [research/research_config.json](../research/research_config.json) | Shared config: brand tone, GPT model, sheet id |
| [docs/reel_creation.md](./reel_creation.md) | Manual reel SOP — input to the GPT prompt |

---

## Required env vars (`.env`)

| Var | Purpose |
|---|---|
| `OPENAI_API_KEY` | GPT-4o (research) + TTS (voiceover) |
| `GOOGLE_SHEET_ID` | spreadsheet to read/write |
| `GOOGLE_SHEET_REELS_NAME` | tab name (default `"Reels"`) |
| `YOUTUBE_API_KEY` | research enrichment |
| `INSTAGRAM_IG_USER_ID` | publish target |
| `INSTAGRAM_ACCESS_TOKEN` | Graph API auth |
| `IMGBB_API_KEY` | (image post fallback — not used for reels) |
| `google_service_account.json` | file at repo root — Drive + Sheets auth |

---

## Known pitfalls

1. **HyperFrames does NOT mux audio.** The renderer always outputs a silent MP4. Step 4 (`ffmpeg` mux) is not optional — without it the posted reel is silent. Step 5 (`ffprobe`) verifies both `h264` video and `aac` audio streams are present.
2. **Multiple `<video>` elements with `#t=START,END` URL fragments cause "video metadata not ready" errors** in headless Chrome. The `montage_hook` template uses 6 `<img>` elements (no video), so this doesn't apply there. If `five_beat` is later upgraded to use background video, pre-cut clips per scene instead of using URL time fragments.
3. **Windows render workers pinned at 2** in `package.json`. Don't bump.
4. **Instagram `instagram_content_publish` permission** is currently pending on the FB App side. While that's blocked, use `--no-publish` to test locally end-to-end (everything through the muxed MP4 works).
5. **PowerShell expands `$`.** When the topic contains a literal `$`, wrap the argument in single quotes and escape: `'How creators make `$10K/month'`. Otherwise the shell drops the `$10` and the row gets garbage like "0K/month".
6. **Voiceover timing must align with the reel template.** If you edit `Voiceover Lines (JSON)` start times in the Sheet, the visual scenes don't move — the audio just lands at different points on the same fixed timeline. Keep starts within the 30s window and in monotonic order.

---

## Verification

End-to-end smoke test (no Instagram):

```powershell
# 1. Research
python scripts/research_topic.py "How faceless creators make money with AI"

# 2. Open the printed URL, edit if needed, flip Status -> Ready

# 3. Build local MP4 only (no IG)
python scripts/build_and_publish_reel.py --no-publish

# 4. Inspect the muxed MP4
ffprobe -v error `
  -show_entries format=duration `
  -show_entries stream=codec_type,codec_name,width,height `
  -of default renders/<slug>.mp4
# Expected: duration~30, 1080x1920, h264 video + aac audio
```

Per-component testing:

```powershell
# voiceover only, from a row that already has Voiceover Lines (JSON)
python scripts/generate_reel_voiceover.py --from-sheet 12 --slug test

# render only (no voiceover, no publish)
python publisher/reel_generator.py --from-sheet 12 --quality draft

# upload to Drive only, skip IG (publish_reel --dry-run)
python publisher/publish_reel.py --video renders/test.mp4 --caption "test" --dry-run
```

---

## Change log

When you change the pipeline, append a one-line note here so future-you knows when the doc was last reconciled with reality.

- **2026-05-02** — Initial plan written. Reels live on dedicated `Reels` tab (Sheet has 3 tabs: posts, carousels, reels). 21-column schema. Both templates implemented. Publish path wired but blocked on IG `instagram_content_publish` permission.
