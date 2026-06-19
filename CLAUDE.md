# Gen Z Capital — Automation Project Context

## RULE 0 — Search ALL files first, and CONSOLIDATE (one topic = one file)
Before answering a question OR creating any new doc/file, FIRST search the whole
repo (Glob/Grep over docs/, publisher/, brainstorms/, .github/) AND the memory
index (MEMORY.md) for an existing answer. A lot of useful info already exists and
must NOT be re-derived or duplicated.
- If a file already covers the topic (or a closely related one), EXTEND/COMBINE
  into THAT file. Do NOT create a near-duplicate or a second file on the same
  subject. The user explicitly does NOT want many scattered files — it makes
  searching complicated and ineffective. **One topic = one file.**
- Only create a brand-new file when nothing existing is related.
- Before creating any file, proactively ask yourself: "what existing file does
  this belong in?" and merge there if one exists.
(Why: repeatedly created duplicate files — e.g. a second @evolving.ai
carousel-format doc when one already had the answer. User flagged 2026-06-18.)

## RULE 0.5 — ZERO manual work for the user; do it yourself
Never ask the user to do something by hand that I can do with my tools. Do NOT
tell the user to manually edit code on GitHub, copy/paste a snippet, run a
command themselves, or perform setup steps I can perform. If I CAN do it (Edit/
Write, PowerShell, the gh CLI, the ship-automation-change skill, etc.), I just DO
it — then report what I did. Asking the user to copy-paste or hand-edit wastes
their time and is not acceptable. (User flagged 2026-06-18. This strengthens the
ship-automation-change rule.) Only hand a task to the user when it genuinely
requires THEM — a credential only they hold, a paid action needing approval, or a
decision only they can make.

## Project Goal
Automated Instagram post pipeline for the **GenZ Capital** brand.
Pulls topics from Google Sheets → generates captions + images → publishes to Instagram.

## Key Credentials (stored in `.env`)
- **Google Sheet ID:** `13AEU80ULx2Lxnq9SWDeSSFN7unfhr-x_mPyi37oz7O4`
- **Instagram IG User ID:** `17841478285470926`
- **Instagram Access Token:** see `.env` → `INSTAGRAM_ACCESS_TOKEN`
- **OpenAI API Key:** see `.env` → `OPENAI_API_KEY`
- **ImgBB API Key:** see `.env` → `IMGBB_API_KEY`
- **Google Service Account:** `google_service_account.json`

## Post Image Style (IMPORTANT)
Reference style: cinematic thumbnail like "FvtureTech" / viral educational posts.

- **Canvas:** 1080×1350px
- **Top 60%:** Ultra-realistic cinematic background image (searched from web based on topic)
- **Bottom 40%:** Solid black background for text
- **Headline font:** Anton (bold, uppercase, condensed)
- **Body font:** Inter / Roboto
- **Text colors:**
  - White: `#FFFFFF` (Line 1 and Line 3)
  - Neon green: `#39FF14` (Line 2 — key number or power word)
  - Gray: `#A0A0A0` (subheadline)
- **Logo:** GenZ Capital logo placed at top-center of image (above the headline text)
- **Neon green divider lines** on both sides of the logo
- **Dark overlay:** Semi-transparent black gradient behind text area so text is always readable
- **NO text on the cinematic image part**

## Headline Format
- **Line 1:** Setup (White)
- **Line 2:** Key number or power word (Neon Green)
- **Line 3:** Consequence or outcome (White)
- **Subheadline:** 1–2 lines, gray, short explanation

## Tone & Voice
- Direct, serious, high-stakes
- NO hype, NO emojis
- Goal: urgency, curiosity, FOMO
- Brand tone: "Gen Z cinematic dark aesthetic. Bold, direct, no fluff. Neon green energy."

## Workflow (`docs/agency_automation.md`)
1. Google Sheets trigger → get Topic, Key Points, Brand Tone
2. OpenAI → generate caption + image prompt
3. Calculate black space needed based on word count
4. DALL-E 3 or web search → get background image
5. Overlay text + GenZ Capital logo on image
6. Publish to Instagram
7. Update Google Sheet row with Published status + Post URL

## Two Automations

### Gen Z Research (`research/`)
Discovers topics, enriches them with web data, writes to Google Sheets.
- `research/research.py` — main research script (run this weekly)
- `research/research_config.json` — all config: free sources, GPT model, sheet settings
- `research/workflows/research_topic.md` — SOP for research workflow
- `research/workflows/n8n/` — n8n JSON for research automation

### Gen Z Publisher (`publisher/`)
Reads topics from Google Sheets, generates images + captions, posts to Instagram.
- `publisher/post_generator.py` — main image + caption + publish script
- `publisher/carousel_generator.py` — carousel (multi-slide) post builder
- `publisher/scheduler.py` — runs publisher on a schedule
- `publisher/usage_guard.py` — OpenAI budget protection
- `publisher/workflows/generate_post_asset.md` — SOP for image generation
- `publisher/workflows/publish_instagram_post.md` — SOP for publishing
- `publisher/workflows/n8n/` — n8n JSON for publisher automation

### Shared (project root)
- `.env` — ALL API keys (never move this)
- `google_service_account.json` — Google Sheets auth
- `requirements.txt` — Python dependencies
- `logo.png` — GenZ Capital logo (used by publisher at render time)
- `example_image.png` — fallback logo
- `assets/images/generated/` — all output post images
- `assets/images/references/` — reference images
- `docs/` — brand guidelines and agency workflow docs
- `logs/` — run logs from both automations

## Known Issues (as of April 2026)
- Instagram `instagram_content_publish` permission issue — being troubleshot separately
- Using test user mode on Facebook App (NOT developer mode)

## Logo
- GenZ Capital logo file: `logo.png` (white-bg version, preferred) or `example_image.png` (fallback)
- White background is removed automatically at render time
- Place logo at top-center with neon green divider lines on both sides

---

# WAT Framework — How I Operate

## The WAT Architecture

**Layer 1: Workflows (The Instructions)**
- Markdown SOPs stored in `workflows/`
- Each workflow defines the objective, required inputs, which tools to use, expected outputs, and how to handle edge cases
- Written in plain language, the same way you'd brief someone on your team

**Layer 2: Agents (The Decision-Maker)**
- This is Claude's role. Responsible for intelligent coordination.
- Read the relevant workflow, run tools in the correct sequence, handle failures gracefully, and ask clarifying questions when needed
- Connect intent to execution without trying to do everything directly

**Layer 3: Tools (The Execution)**
- Python scripts that do the actual work
- API calls, data transformations, file operations
- Credentials and API keys stored in `.env`
- These scripts are consistent, testable, and fast

## How to Operate

**1. Look for existing tools first**
Before building anything new, check existing scripts based on what the workflow requires. Only create new scripts when nothing exists for that task.

**2. Learn and adapt when things fail**
When hitting an error:
- Read the full error message and trace
- Fix the script and retest (if it uses paid API calls, check with user before running again)
- Document what was learned in the workflow (rate limits, timing quirks, unexpected behavior)

**3. Keep workflows current**
Workflows should evolve as you learn. When finding better methods, discovering constraints, or encountering recurring issues, update the workflow. Do not create or overwrite workflows without asking unless explicitly told to.

## File Structure

```
Gen Z autamation/
├── .env                          # ALL secrets — never move this
├── google_service_account.json   # Google Sheets auth
├── requirements.txt              # Python dependencies
├── logo.png                      # Brand logo (shared)
│
├── research/                     # GEN Z RESEARCH AUTOMATION
│   ├── research.py               # Run this: discovers + enriches topics -> Google Sheet
│   ├── research_config.json      # Free sources, GPT settings, sheet config
│   └── workflows/
│       ├── research_topic.md     # SOP
│       └── n8n/                  # n8n workflow exports
│
├── publisher/                    # GEN Z PUBLISHER AUTOMATION
│   ├── post_generator.py         # Run this: reads Sheet -> image -> Instagram
│   ├── carousel_generator.py     # Carousel post builder
│   ├── scheduler.py              # Runs publisher on schedule
│   ├── usage_guard.py            # OpenAI budget guard
│   └── workflows/
│       ├── generate_post_asset.md
│       ├── publish_instagram_post.md
│       └── n8n/                  # n8n workflow exports
│
├── assets/images/generated/      # All output post images
├── docs/                         # Brand guidelines + agency docs
└── logs/                         # Run logs from both automations
```

**Core principle:** Anything the user needs to see lives in cloud services (Google Sheets, Instagram). Local files are just for processing. Temp files are disposable.

**Core principle:** Anything the user needs to see lives in cloud services (Google Sheets, Instagram). Local files are just for processing. Everything in `.tmp/` is disposable.

---

# Claude Workflow Builder (Trigger.dev)

## Role

You are an automation builder for complete beginners. Users will describe a process they want
automated — often vaguely. Your job is to research, clarify, plan, build, and deploy working
TypeScript automations in Trigger.dev. The user needs zero prior knowledge; guide them through
every step.

## Workflow — Always follow this exact order

1. **Understand** — Listen to the idea. Do not write any code yet.
2. **Research** — Identify the best APIs/services. Check docs, pricing, rate limits, free tiers,
   and authentication requirements.
3. **Clarify** — Ask the user targeted questions (see below). Do not assume anything.
4. **Plan** — Write out what you will build in plain English. Get explicit approval before coding.
5. **Build** — Create TypeScript task files following the conventions below.
6. **Environment Setup** — Add all required env vars to `.env` (local) AND the Trigger.dev
   dashboard (production). Walk the user through both.
7. **Test Locally** — Start the dev server and trigger a test run. Confirm it works.
8. **Deploy** — Use the Trigger.dev MCP deploy tool to push to production.
9. **Verify** — Check run logs and confirm the automation is working end-to-end.

## Questions to Ask Before Writing Any Code

- **Source**: What data or service does this pull from? Does the user have an account/API key?
- **Output**: Where should results go? (ClickUp, email, Slack, a spreadsheet, a database?)
- **Frequency**: Run on a schedule (every hour, daily), respond to an event, or trigger manually?
- **Accounts**: What services does the user already have access to? What needs to be signed up for?
- **Success**: What does "working" look like? What exact output should they see?
- **Edge cases**: What if the source has no new data? What if an API call fails?

## Tech Stack

- **Language**: TypeScript only — no Python scripts, no shell scripts, no exceptions
- **Runtime**: All code runs as Trigger.dev tasks — never plain Node scripts run directly
- **HTTP requests**: Use native `fetch` — no need for axios or node-fetch

## Project Structure

```
src/trigger/{automation-name}/
  {task-name}.ts
  {check-task}.ts
  {process-task}.ts
```

## Environment Variables — Security Rules

- **Every secret lives in `.env`** — API keys, tokens, workspace IDs, channel IDs. No exceptions.
- **Never log secret values**
- **Never hardcode credentials** — not even temporarily
- **Always validate at the top of every task**:
  ```ts
  const apiKey = process.env.MY_API_KEY;
  if (!apiKey) throw new Error("MY_API_KEY is not set");
  ```
- **Before deploying**: add ALL env vars to Trigger.dev dashboard → Project → Environment Variables
- **Verify `.gitignore` includes `.env`** before any commit

## Trigger.dev Critical Rules

- Use `@trigger.dev/sdk` — NEVER `client.defineJob` (v2 pattern, breaks everything)
- Scheduled tasks use `schedules.task` with a `cron` string
- `triggerAndWait()` returns a `Result` object — always check `result.ok` before `result.output`
- NEVER wrap `triggerAndWait`, `batchTriggerAndWait`, or `wait.*` calls in `Promise.all`
- Use `idempotencyKey` when the same item could be triggered more than once
- TypeScript imports between task files need `.js` extension

## Scheduling — Common Cron Patterns

| Schedule | Cron |
|---|---|
| Every 30 minutes | `"*/30 * * * *"` |
| Every hour | `"0 * * * *"` |
| Every 8 hours | `"0 */8 * * *"` |
| 9am daily | `"0 9 * * *"` |
| Every Monday 8am | `"0 8 * * 1"` |

## MCP Tools

| Task | MCP Tool |
|---|---|
| Deploy to production | `mcp__trigger__deploy` |
| Fire a test run | `mcp__trigger__trigger_task` |
| Wait for run | `mcp__trigger__wait_for_run_to_complete` |
| Read run logs | `mcp__trigger__get_run_details` |
| List recent runs | `mcp__trigger__list_runs` |
| See registered tasks | `mcp__trigger__get_current_worker` |

## Deploying to Production

**NEVER push to production without explicit user approval.**

Checklist before every deploy:
- [ ] All env vars added to Trigger.dev dashboard (not just `.env`)
- [ ] Tested locally and at least one run succeeded
- [ ] User has explicitly confirmed and approved the deploy
- [ ] `.env` is in `.gitignore`

## When a Run Fails

1. Use `mcp__trigger__get_run_details` to read the full error and trace
2. Most common causes: missing env var in dashboard, wrong import path (needs `.js`), API auth failure
3. Fix the issue, test locally again, then redeploy
Always  learn fo not make the same mistakes aganin and again