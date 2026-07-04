---
name: automation-engineering
description: >-
  Builds and troubleshoots automations with the logic understood first. Use
  when creating ANY new automation, adding/changing logic in an existing one,
  or when an automation fails, misbehaves, produces wrong output, or needs
  debugging (reels, carousels, media finder, publishing, research, n8n,
  GitHub Actions, sheet logic). Locates where the bulk of the logic lives via
  system_map.md, reads real run logs before guessing, fixes root causes, and
  ships end-to-end.
---

# Automation Engineering — Understand the Logic First

Two workflows: **BUILD** (new automation / new logic) and **TROUBLESHOOT**
(existing automation broken or wrong). Both share one law:

> Never touch code before you can state, in one paragraph, what the
> automation's logic IS — trigger → inputs → decisions → outputs → state.

Where the logic lives for every existing automation: [system_map.md](system_map.md).
Read it FIRST in both workflows — do not rediscover the pipeline by grepping blind.

## Hard rules (apply to both workflows)

- **Evidence over guessing.** `gh` CLI is installed and authed — read failed
  Actions runs yourself (`gh run list`, `gh run view <id> --log-failed`).
  n8n is readable via the n8n-mcp tools (`n8n_executions`, `n8n_get_workflow`).
  Never ask the user to paste an error.
- **A local edit changes NOTHING that runs.** Cloud runs use committed+pushed
  code. Every change ends with commit + push (see ship-automation-change
  skill; push via PowerShell, not Bash).
- **No paid API calls without asking** (OpenAI quota is dead anyway — Claude
  writes copy directly). Free sanity checks are always allowed.
- **Fix the cause, not the symptom.** A retry/try-except around a failure you
  don't understand is not a fix.
- **Close the loop.** After any fix or build, record what was learned: update
  the relevant workflow doc or system_map.md if the map changed. One topic =
  one file — extend existing docs, never create near-duplicates.

## BUILD workflow — creating an automation or new logic

Copy this checklist and check items off as you go:

```
Build Progress:
- [ ] 1. Understand the goal (restate it; no code yet)
- [ ] 2. Search first: existing tools/scripts/docs that already do part of this
- [ ] 3. Design the logic on paper: trigger → inputs → steps/decisions → outputs → failure handling → state
- [ ] 4. Clarify ONLY decisions the user must make (accounts, money, taste)
- [ ] 5. Build, reusing existing modules (media_sources/, caption_builder, notify_email, ...)
- [ ] 6. Test free & locally; validate edge cases (empty input, API failure, re-run/idempotency)
- [ ] 7. Ship: commit + push; wire the trigger (workflow YAML / n8n / cron)
- [ ] 8. Verify a real run end-to-end (gh run watch / logs); add it to system_map.md
```

Design rules for step 3:

- Every automation must answer: **what triggers it, what claims the work
  (so it can't run twice), what marks it done, and what happens on failure.**
  The reel state machine (`Ready to Run → Building → Ready to Post`) is the
  house pattern — distinct trigger word vs done word prevents duplicate runs.
- Address sheet rows by **stable key (Topic string), never row number** —
  row indexes drift on re-sort.
- Secrets live in `.env` locally and GitHub Secrets in the cloud — both, or
  the cloud run dies. Never put `http_proxy`/`https_proxy` in `os.environ`
  (it breaks Google Sheets auth).
- Make failures loud and specific: log WHICH step failed and WHY, write a
  failure status back to the sheet, don't swallow exceptions.

## TROUBLESHOOT workflow — automation broken or output wrong

Copy this checklist and check items off as you go:

```
Troubleshoot Progress:
- [ ] 1. Get the real evidence (exact error/wrong output — logs, not memory)
- [ ] 2. Locate the logic: system_map.md → entry point → grep the error string / trace the failing step
- [ ] 3. State the logic of that code path in one paragraph (trigger → data in → decision → data out)
- [ ] 4. Form ONE hypothesis that explains ALL the evidence
- [ ] 5. Verify the hypothesis cheaply (read code / re-run one free step / print the actual data)
- [ ] 6. Fix the root cause
- [ ] 7. Sanity-check the fix (free), then commit + push
- [ ] 8. Trigger or await a real run; confirm the symptom is gone
- [ ] 9. Record the lesson (workflow doc / system_map.md) so it never repeats
```

Step 1 — where evidence lives:

| Symptom surface | How to read it |
|---|---|
| GitHub Actions run failed | `gh run list --limit 10`, `gh run view <id> --log-failed` |
| n8n didn't fire / fired wrong | `n8n_executions` / `n8n_get_workflow` MCP tools |
| Sheet row stuck in a status | Read the row: status word + timestamps tell you which step died |
| Output rendered but wrong | Download the artifact/Drive file and LOOK at it |

Step 2 — locating the logic: the entry point per automation is in
[system_map.md](system_map.md). From the entry script, follow the imports to
the module that owns the failing decision (scoring, captioning, rendering,
publishing) — the bulk of logic is in `publisher/`, not the YAML. Grep the
exact error string or the sheet status word to land on the line that wrote it.

Step 4 — a hypothesis that explains only half the evidence is wrong. A
signal that pattern-matches a known past failure (see Known traps in
system_map.md) may still have a different cause this time — confirm before
re-applying an old fix.

## Definition of done (both workflows)

- [ ] Logic understood and stated before coding
- [ ] Root cause fixed / design covers failure + idempotency
- [ ] Sanity-checked free of paid calls
- [ ] Committed + **pushed** (PowerShell), commit hash reported
- [ ] Real run verified, or next scheduled run identified
- [ ] Lesson recorded; system_map.md updated if the map changed
- [ ] Zero manual steps left for the user
