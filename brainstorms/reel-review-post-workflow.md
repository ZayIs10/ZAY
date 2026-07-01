# Brainstorm: Reel → Review → Post Workflow

_Started: 2026-06-08 — fill the Summary in at the end._

## Summary
<One-paragraph synthesis of the captured knowledge. Write this last.>

## Goal
Define exactly how the GenZ reel pipeline should behave from "reel finished rendering"
through "posted to Instagram/YouTube" — including the review/approval step the user wants
in the middle (draft + email, do NOT auto-post).

## Process / Algorithm
<The step-by-step process or algorithm, in order. The "how it actually works".>
- Current known flow: Google Sheet (Status="Ready to Run", Post Type=reel) → n8n manual
  Execute → IF node → Claim (Building) → HTTP dispatch to GitHub → GitHub builds reel
  (tweet_card_reel.py, selects row by Topic) → uploads to Drive → Status="Ready to Post"
  → emails user a review draft (Drive link + Post Caption incl. hashtags).

## Key Decisions
- **Decision:** How GitHub selects which row to build → **Chosen:** by Topic string (--topic),
  not row number — _Why:_ row numbers drift when the sheet is re-sorted, caused wrong-topic renders.
- **Decision:** Where caption+hashtags come from → **Chosen:** the sheet's Post Caption column,
  which already contains both — _Why:_ user keeps them together in one field.
- **Decision:** Post automatically or draft for review → **Chosen:** draft + email only, no auto-post
  — _Why:_ user wants to review before posting.

## Q&A Log
<Append every question and answer here, in order, as the session runs.>

## Key Highlights
<The most important / non-obvious insights worth surfacing for whoever builds from this.>

## Open Flags
- [ ] Confirm the exact topic/scope of this grill with the user.
