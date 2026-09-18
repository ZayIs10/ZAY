# Workflow: Publish Instagram Post

## Objective
Publish finalized image + caption to Instagram and update Google Sheets status.

## Required Inputs
- Final image artifact
- Final caption text
- Source sheet row identifier
- Instagram credentials from `.env`

## Tools To Use
- Existing Instagram publish path (Graph API or n8n workflow)
- Existing Google Sheets update path
- Optional uploader step (e.g., ImgBB) if required by current pipeline

## Steps
1. Validate publish payload (image exists, caption non-empty, row id present).
2. Upload image to required hosting endpoint if pipeline requires a public URL.
3. Create Instagram media container and publish.
4. Capture resulting post id / permalink.
5. Update Google Sheets row:
   - `status=Published`
   - `published_at=<timestamp>`
   - `post_url=<url>`
6. Return publish result object.

## Expected Outputs
- Published post URL or media ID
- Updated sheet row status
- Execution log for traceability

## Edge Cases
- Missing permission/token errors: stop and mark row `publish_failed`.
- Partial failure (published but sheet update failed): retry sheet update separately.
- Duplicate publish risk: if row already `Published`, do not republish unless explicitly forced.


## The approval word is "Approved" (changed 2026-09-18)

To publish a rendered reel, type **`Approved`** on its row — in either the
live `Status` column or the visible legacy `Published` column F. The 3:00 AM
MYT cron then posts ONE approved reel per day, top row first.

It used to be `Publish`, and that one word caused a silent outage:

- The LIVE n8n Workflow B had an extra IF node (`If2`, matching
  `Published == "Publish"`) wired straight into the BUILD path. It was added
  in the n8n UI and **never existed in the committed JSON** — the same
  live-vs-repo drift as the 2026-08-02 incident.
- So typing the approval word (a) dispatched a reel build, and (b) the claim
  node immediately wrote `Building` over the cell. The approval was erased
  about a second after it was typed.
- The dispatch also came from a path that never ran `Set - Extract Row
  Fields`, so the build got an empty topic and died with
  `Neither topic nor row_index provided` (runs 34991853412 / 34991853435).
- Net effect: rows 84 and 85 were approved on 15-Sep and never published,
  while every nightly run exited GREEN reporting nothing to do.

`Approved` shares no prefix with `Building` or `Published`, so no gate can
confuse them again. **`Publish` is still accepted** (`LEGACY_APPROVED_STATUS`)
so older rows keep working — only what we ASK for changed.

Fix applied: `If2` removed from the live workflow; approving a reel can no
longer start a build. If a build ever starts from an approval again, check the
LIVE graph against `publisher/workflows/n8n/tweet_card_reel_workflow.json`
first — the live one is what runs.

## Rows stranded at "Building" (the sleeping-laptop bug, 2026-09-18)

All reel rendering happens on the self-hosted PC (`marc-pc`); the cloud path
is skipped because the proxy probe fails. When the laptop sleeps mid-build the
runner dies instantly ("The self-hosted runner lost communication with the
server", no log), and the Python that would have written `Render Failed` dies
with it — so the row keeps the claim word `Building` **forever**. Nothing
polls `Building`: not n8n, not `proxy_recovery.py`, not the build itself.

September evidence: 5 runs died that way (each within seconds of a Windows
sleep event) and 9 more were cancelled after waiting 24 h for a runner.
Motivation rows 5 and 7 sat stranded with no MP4 until 2026-09-18.

Fix: `.github/workflows/rescue_stuck_reels.yml` →
`publisher/stuck_row_recovery.py`, every 4 h on **ubuntu-latest** (it only
touches the sheet, so it runs while the laptop is off). A row at `Building`
for more than 60 min goes back to `Ready to Run`, capped at 3 automatic
attempts, then `Render Failed`. The attempt counter lives inside
`Media Status` as `[requeue n=<n> since=<iso>]`, so no new column is needed.

Consequence for the user: closing the laptop mid-render is now harmless — the
row is requeued and builds next time the PC is online. Opening the laptop was
never the problem; a queued job simply waits for the runner.

## Never hand-edit the visible column of a row n8n has claimed

The visible `Published` column is BOTH what you type into and what n8n's gate
reads. If a row says `Building` and you put a trigger word back into that
cell, n8n dispatches the row again on its next poll — once a minute. Seen
2026-09-18 while testing Motivation rows: two duplicate builds fired.

It is not dangerous (the build refuses to re-render a row that already has a
Reel MP4 URL, and repairs the status to `Ready to Post`), but it wastes the
laptop's runner. Rule: once a row is `Building`, leave it alone — let the
build write the next word, or let the 4-hourly rescue job requeue it.

## Notification emails (never a silent publish — 2026-09-16)

The Reels tab and the Motivation tab share ONE scheduler
(`.github/workflows/publish_due_reels.yml` → `publisher/publish_due_reels.py`,
one post per day total, Reels rows before Motivation rows). The user cannot
watch a 3 AM cron, so every planned post produces emails, all via
`publisher/notify_email.py` (Gmail SMTP secrets `GMAIL_ADDRESS` /
`GMAIL_APP_PASSWORD`, recipient `NOTIFY_TO`):

| When (MYT) | Cron (UTC) | Mode | Email | Writes sheet? |
|---|---|---|---|---|
| 3:00 PM | `0 7 * * *` | `--heads-up` | `[GenZ] PLANNED: <topic> publishes to Instagram at <slot>` — tab, row, Drive link, caption, and the rest of the queue; says how to stop it (clear "Approved"). If the token is dead the subject becomes `[GenZ WARNING] Instagram token is DEAD — …` so there are 12 h to fix it. **No queue = no email.** | No |
| 3:00 AM | `0 19 * * *` | publish | `[GenZ] PUBLISHED on Instagram: <topic>` + post URL after a successful post; `[GenZ ALERT] … failed to publish` on a per-row failure; `[GenZ ALERT] Instagram token EXPIRED` when the pre-flight fails. | Yes |
| any | job crash | either | `[GenZ ALERT] 3am MYT publish run FAILED` / `… 3pm MYT heads-up run FAILED` from the workflow's `if: failure()` step. | No |

The workflow reads `github.event.schedule` to tell the two crons apart; the
manual "Run workflow" button has a `mode` input (`publish` / `heads-up` /
`dry-run`). `--dry-run` lists the queue and sends nothing (used for tests).
The slot time in the emails is computed from `PUBLISH_UTC_HOUR` in
`publish_due_reels.py` — change both that constant and the cron together.

## Access token (the thing that actually breaks publishing)

Meta long-lived user tokens last **60 days**. The one in the
`INSTAGRAM_ACCESS_TOKEN` GitHub secret expired **18-Aug-2026** and every
nightly `publish_due_reels` run stayed green while posting nothing for a week
(rows 80–83 got marked "Publish Failed"). Fixed 2026-08-24:

- `publish_due_reels.py` pre-flights the token (`debug_token`) and **aborts
  with exit 2 + a "token EXPIRED" email before touching any row**. Approved
  rows stay `Approved` and drain automatically once the token is replaced.
- `publish_due_reels.yml` now treats `check_ig_token.py` as a hard gate → a
  dead token is a **red run**, never a green one.
- `refresh_ig_token.yml` (Mondays 06:00 UTC) exchanges the token for a fresh
  60-day one whenever < 30 days remain (`fb_exchange_token`). Needs secrets
  `FB_APP_ID` (set: `989601526736983` = app "Gen Z publisher"),
  `FB_APP_SECRET` (App Dashboard → Settings → Basic → App Secret) and
  `ACTIONS_SECRETS_PAT` (GitHub PAT, `repo` scope — `GITHUB_TOKEN` cannot
  write secrets).

**An already-expired token can NEVER be refreshed** — a new one must be
generated by hand (Graph API Explorer → app "Gen Z publisher" → user token
with `instagram_basic, instagram_content_publish, pages_show_list,
pages_read_engagement, business_management` → Exchange for long-lived token),
then: `gh secret set INSTAGRAM_ACCESS_TOKEN` and update `.env`.
Verify with `python publisher/check_ig_token.py`.

**Update 2026-08-24:** the secret now holds a Business Manager **System User**
token (`debug_token` → `type: SYSTEM_USER`, `expires_at: 0` = never expires,
all publish scopes). So no more 60-day expiry; `refresh_ig_token.yml` is kept
manual-only (cron commented out). If a 60-day *user* token is ever used
again, re-enable that cron and add `FB_APP_SECRET` + `ACTIONS_SECRETS_PAT`.
