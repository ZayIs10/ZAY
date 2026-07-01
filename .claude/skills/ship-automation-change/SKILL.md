---
name: ship-automation-change
description: >-
  Use whenever the user asks to change, fix, reinforce, or update ANYTHING about
  the automation/pipeline (media finder, reel builder, captions, scoring,
  workflows, GitHub Actions, n8n, sheet logic, etc.). Guarantees the change is
  shipped end-to-end to the place that actually RUNS it — committed and pushed
  to GitHub — in one go, with zero manual work left for the user. Also covers
  "did you actually change it on GitHub?" type questions.
---

# Ship Automation Change — End-to-End, No Manual Work

The user's standing rule (do NOT violate it):

> "Whenever I ask you to change the automation, you must change it on GitHub
> (or whatever end actually runs it) — all in one set. Be proactive. I do NOT
> want any manual work. If you can do it, do it."

This project's automation runs in the **cloud**, not on the laptop:
- GitHub Actions checks out and runs the repo's **committed + pushed** code
  (`.github/workflows/build_tweet_card_reel.yml`, `build_carousel.yml`).
- n8n fires those workflows via `repository_dispatch`.

**Editing a local file changes NOTHING that runs.** A local edit only reaches
the live automation after `git commit` + `git push`. This is the #1 mistake to
avoid: never leave a fix sitting uncommitted and call it done.

## The rule: every automation change ends with a push

When the user asks to change/fix/reinforce ANY automation behavior, the task is
NOT complete until the change is **live where it runs**. Always carry it all the
way through in one set:

1. **Make the code/config change** in the right file(s).
2. **Sanity-check** it (run the affected script or a quick repro locally if it's
   free/safe; never spend paid API calls without asking).
3. **Commit** the changed files with a clear message
   (end with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`).
4. **Push to GitHub** — this is the step that makes it real. Do not skip it,
   do not ask "want me to push?" — just push (the user said be proactive / no
   manual work). Only pause if the push needs a decision the user must make.
5. **Confirm it's live**: report the commit hash + that it's pushed, and which
   automation will now use it on the next run.

## Pushing on Windows (auth gotcha — learned the hard way)

`git push` from the **Bash tool** fails with
`Password authentication is not supported` / `Authentication failed` — Bash
can't reach the Windows Git Credential Manager.

**Always push using the PowerShell tool instead** — it triggers Git Credential
Manager and succeeds:

```
# PowerShell tool:
cd "c:\Users\Marc\Desktop\Gen Z autamation"; git push origin main
```

`gh` CLI is not installed and SSH keys are not set up, so PowerShell + the
credential manager is the working path. (Commit can be done from either tool;
only the **push** must go through PowerShell.)

## What counts as "the automation"

Anything whose behavior shows up in a real run: media finder / scoring,
tweet-card or carousel reel builders, caption/script generators, the GitHub
workflow YAMLs, brand configs, sheet-column logic. If a change to one of these
isn't pushed, it isn't shipped.

## Sheet content vs. automation code

Writing caption/script **content** into the Google Sheet is a live change
already (the sheet is the cloud source of truth) — no push needed for that.
But changing the **code that generates or renders** that content IS automation
and must be pushed. When in doubt: if GitHub runs it, push it.

## Definition of done

- [ ] Change made in the correct file(s)
- [ ] Sanity-checked (no paid API calls without asking)
- [ ] Committed with a clear message
- [ ] **Pushed to GitHub via PowerShell** (verified: `local..remote main -> main`)
- [ ] Reported commit hash + which automation now uses it
- [ ] Nothing left for the user to do by hand
