# System Map — Where the Logic Lives

Verified 2026-07-04. When an automation changes shape, update THIS file.

## Contents
- Tweet-card reels (build)
- Carousels (build)
- Media finder
- Reel publishing
- Legacy single-image posts
- Research / topic intake
- Shared modules
- Sheet contract
- Known traps

## Tweet-card reels (build)

- **Trigger:** n8n `repository_dispatch` when a `reels`-tab row hits `Ready to Run`
- **Runner:** `.github/workflows/build_tweet_card_reel.yml` on `ubuntu-latest`
  through a residential proxy (YouTube blocks datacenter IPs; self-hosted
  Windows runner `[self-hosted, windows, genz-pc]` is the documented fallback)
- **Entry:** `python publisher/tweet_card_reel.py --topic "$TOPIC"` — selects
  the row by **Topic string**, never row number
- **Logic:** `publisher/tweet_card.py` (card render) ·
  `publisher/hook_opener.py` (viral hook opener: one whole clip from
  viralhooks.org's ~340-hook free library plays FULL-SCREEN first, CLEAN —
  no card until the body starts; deterministic per Topic; best-effort — never
  fails a build; `DISABLE_VIRAL_HOOK=1` kills it, `VIRAL_HOOK_SLUG` forces
  one; plain HTTPS, no proxy) ·
  `publisher/media_finder.py` + `publisher/media_sources/` (clip pick:
  `youtube.py`, `scoring.py` — relevance −60 sink, `transcript_picker.py`,
  `pexels.py`, `google_images.py`, `brand_official.py`) ·
  `publisher/caption_builder.py` (`_ensure_captions` self-fills bare rows,
  never overwrites hand-written copy; the Reel Caption quotes the source
  video's OPENING spoken lines via `transcript_picker.fetch_transcript` so
  the card matches the footage — the reel always shows the clip's first
  ~60s, so caption and clip come from the same part of the video) ·
  `publisher/notify_email.py`
  (Drive link + caption review email via Gmail SMTP)
- **Format:** single clip + tweet-card overlay, LOCKED (multi-beat code in
  `publisher/beats.py`/`beat_media.py` exists but is unused)
- **State machine:** `Ready to Run` → `Building` → `Ready to Post`
- **Output:** rendered MP4 → Google Drive (OAuth as genzcapital999; the
  service account has no storage quota)

## Carousels (build)

- **Trigger:** n8n `repository_dispatch` when a `carousels`-tab row is ready
- **Runner:** `.github/workflows/build_carousel.yml` on `ubuntu-latest`
- **Entry:** `python publisher/carousel_templates.py from-sheet` (drafts +
  validates the slide spec; 3 locked templates: tutorial/listicle/news_hybrid)
  → `python publisher/carousel_image_pipeline.py --spec "$SPEC"` (images)
- **Logic:** `publisher/carousel_format.py` (@evolving.ai layout engine) ·
  `publisher/cover_director.py` (6 cover archetypes) ·
  `publisher/source_logo.py` (story-company logo per slide, Google favicon)
- **Review:** `.github/workflows/send_carousel_review.yml` →
  `publisher/carousel_review.py`
- **Format spec:** `docs/evolving_ai_carousel_format.md`

## Media finder

- **Trigger:** n8n `repository_dispatch` on new sheet row
- **Runner:** `.github/workflows/find_topic_media.yml`
- **Entry:** `python publisher/media_finder.py --all-pending`

## Reel publishing

- **Scheduled:** `.github/workflows/publish_due_reels.yml` — cron `0 12 * * *`
  (= 8pm SGT peak window; check the file, the cron line has been toggled) →
  `publisher/check_ig_token.py` then `publisher/publish_due_reels.py`.
  Capped at ONE reel/day; the IG API cannot schedule (any
  `scheduled_publish_time` posts instantly), so the cron IS the scheduler.
- **Container retry:** `.github/workflows/publish_reel_container.yml` →
  `publisher/publish_reel.py --container-id` (`--check-only` to probe)
- **Cross-post:** YT Shorts + IG Reels; handle is `@genzcapital` only

## Legacy single-image posts

- `.github/workflows/publish.yml` → `publisher/post_generator.py`
  (old finance-era image posts; brand has pivoted to AI-tools content)

## Research / topic intake

- **Topic FINDER (auto-discovery, the "what should we make" step):** n8n
  workflow "Gen Z AI Tools - Daily YouTube Short Topic Finder"
  (live id `JHpRFTRB2t7TzqKXweY7b`; source of truth
  `publisher/workflows/n8n/reel_research_workflow.json`). Free sources
  (Reddit hot + 2× YouTube search + TechCrunch RSS) → normalize → **AI-fit
  score** (`Code - Score AI-Fit & Pick Top`: tool +3 / how-to +3 /
  the scorer node `Code - Score AI-Fit & Pick Top`) → writes **BARE** Reels
  rows (`Topic + Key Points + Brand Tone + Status='Draft'`) → emails a
  review summary. **Review gate:** the builder only fires on `Ready to Run`,
  so nothing builds until the user flips a Draft row by hand — the finder can
  never trigger an unwanted build, and it does NO media/caption work
  (disjoint fields from the builder = can't collide).
  - **Niche = HARD GATES (not score bonuses):** a topic must pass TOOL
    (mentions an AI tool) AND ACTION ("here's what you can DO" — how-to,
    prompt trick, best-tool-for-X, usable feature) AND NOT OFF
    (finance/crypto, funding/valuation/IPO, lawsuits/regulation, layoffs,
    make-money/hustle, waitlist/teaser tools, nsfw). Pure announcement
    headlines with nothing to try are dropped. PROMO signal sinks brand
    self-promo ads below the gate. Verified 2026-07-13 against live data.
  - **JUNK gate (YouTube only):** high-view but low-quality noise is
    dropped — hashtag spam (≥2 `#`), non-English tutorials (`kaise banaye`,
    hindi/urdu/bangla), cartoon/meme/vlog/status edits, and engagement-bait
    (`*live test*`, `you won't believe`, `gone wrong`). High views ≠ high
    quality; `order=viewCount` surfaces this junk, so it's filtered by title.
  - **View-count gate (YouTube only):** a video needs **≥5,000 real views**
    to qualify (proves it converts). Fetched via a 2nd API call — see the
    two-step flow below. News/Reddit have no view metric and are exempt.
    More views also rank higher (600k beats 8k on a tie).
  - **Freshness:** anything >7 days old is dropped (when the source dates
    it); ≤2-day items get a bonus and win ties. YouTube `publishedAfter`
    is also 7 days.
  - **Cross-run dedupe (fixes repeat-topics = "never the same topic twice"):**
    a `Google Sheets - Read Existing Topics` node reads the Reels tab's Topic
    column and feeds it to the scorer (Merge input 5) as
    `{existing_topics:[...]}`; the scorer skips any candidate that exactly-
    or fuzzy-matches (≥60% token overlap, year/stop-words stripped) an
    existing topic OR an earlier pick this run, so re-running surfaces the
    NEXT-best fresh topics instead of re-appending last run's picks.
  - **Two-step YouTube flow (for the view gate):** each YouTube branch is
    `search → Code (collect videoIds + snippets) → HTTP videos:list
    (part=statistics, batched ≤50 ids, 1 quota unit) → Code (merge views
    back by id, normalize)`. search=100 units, videos:list=1 unit,
    10k/day free → ~200 units/run, trivial. The normalizer reads the
    snippet map back via `$('Code - YT Tools IDs').first().json.byId` — that
    node-name string must match EXACTLY or the branch silently yields 0.
  - **YouTube key gotcha (root cause of the old repeats):** the Hostinger
    instance blocks `$env` in expressions (`N8N_BLOCK_ENV_ACCESS_IN_NODE`),
    so `{{ $env.YOUTUBE_API_KEY }}` failed → both YouTube nodes returned 0,
    Reddit 403s, leaving ONLY TechCrunch RSS (near-static in 47s) → same 5
    off-niche news picks every run. FIX: the key lives in an n8n
    **httpQueryAuth credential** `vg0eQOmN5gigPYdB` ("YouTube Data API key
    (query)"), NOT in the repo JSON and NOT in `$env`. Never hardcode the
    key into the workflow file — it's committed to a public repo.
  The AI-fit scorer IS "the format of how to find a topic." OpenAI draft
  node was removed (quota dead). **Trigger = Manual only** for now (Schedule
  node disabled); Sheets+Gmail creds shared from Workflow B
  (`AKkpUn5IypddfmfE`, `Wx9U0wyKwyNtqtEu`). Run it from the n8n UI's Manual
  Trigger.
- **Single-topic research (you already know the topic):**
  `scripts/research_topic.py` — enriches ONE given topic, smart YouTube
  transcript pick (free keyword score, no GPT), writes URL + captions to sheet.
  NOTE its OpenAI draft path is also dead — prefer bare rows.
- **Manual seed of N topics:** `.tmp/add_reel_topics_*.py` — appends a
  hand-curated batch as `Ready to Run`; same bare-row pattern.
- **Classifier:** `publisher/format_classifier.py` auto-tags each topic
  reel vs carousel before drafting

## Shared modules

`publisher/caption_builder.py` (captions) · `publisher/notify_email.py`
(review emails, GMAIL_ADDRESS/GMAIL_APP_PASSWORD) · `publisher/usage_guard.py`
(budget guard) · `publisher/compositor.py` (video compositing) ·
`publisher/stage_instagram.py` (IG staging). Secrets: `.env` locally AND
GitHub repo Secrets for cloud runs — a key missing from either side kills
that side's run.

## Sheet contract

- Sheet ID `13AEU80ULx2Lxnq9SWDeSSFN7unfhr-x_mPyi37oz7O4`; tabs: `reels`
  and `carousels` (split 2026-06-28)
- Caption columns: Reel Caption = col 29, Post Caption = col 30
- Status words are the state machine — a distinct trigger word
  (`Ready to Run`) vs done word (`Ready to Post`) prevents duplicate renders

## Known traps (confirm before re-applying an old fix)

- **A "best-effort" try/except can hide a MISSING MODULE for weeks:**
  `transcript_picker.py` lived only on a side branch until 2026-07-04; on
  main every build's transcript import raised ImportError, was swallowed as
  "transcript is best-effort", and captions silently fell back to
  Topic-only — producing on-screen text that didn't match the video. When a
  feature relying on a swallowed import "never seems to fire", check the
  module actually exists on the branch that RUNS.

- **ffmpeg exit-251 / Skipped-No-Video:** `download_ranges` forces ffmpeg
  which ignores the proxy — ranges are dropped when `PROXY_URL` is set
- **NEVER set `http_proxy`/`https_proxy` in `os.environ`** — breaks Google
  Sheets auth; scope any proxy to the specific downloader
- **Pexels clips crash HyperFrames** unless re-encoded with
  `-g 30 -keyint_min 30`
- **OpenAI quota is DEAD** — Claude writes copy directly; don't "fix" a
  caption failure by retrying OpenAI
- **YouTube bot-blocks GitHub datacenter IPs permanently** — cookies don't
  help, a new repo doesn't help; residential proxy or self-hosted runner only
- **`git push` fails from Bash on this machine** — push via PowerShell
  (Git Credential Manager)
- **n8n API keys EXPIRE (~30 days)** — when every n8n MCP call returns
  AUTHENTICATION_ERROR but health_check passes, the JWT in `.claude.json`
  (`N8N_API_KEY`, two places: global `mcpServers` + the `C:/Users/Marc`
  project entry) has expired — decode its `exp` to confirm. Fix: user
  creates a new key in the n8n UI (Settings → n8n API, pick longest/no
  expiry), then update both `.claude.json` entries and reconnect the MCP.
  Reconnecting alone never fixes it. Direct REST
  (`$N8N_API_URL/api/v1/...` with header `X-N8N-API-KEY`) works without
  an MCP restart.
