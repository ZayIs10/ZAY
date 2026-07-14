---
name: optimize
description: Rewrites a messy, plain-text, or vague request into a clean, structured, AI-friendly prompt. Use when the user runs "/optimize <prompt>", prefixes a message with "optimize:" or "optimize prompt:", or asks to "improve/sharpen/rewrite/make better" a prompt they wrote. Outputs the refined prompt as text for review (it does NOT execute the task).
user-invocable: true
argument-hint: "[prompt]"
---

# Optimize — turn a rough request into a structured prompt

Take the user's raw prompt and rewrite it into a clear, structured, actionable
version that a frontier model can act on with zero back-and-forth. **Output the
refined prompt as text for the user to review — do NOT execute the task.**

This is on-demand only. It fires when the user asks for it (`/optimize <prompt>`,
`optimize:` / `optimize prompt:` prefix, or "improve/sharpen/rewrite this prompt").
It does NOT run silently on every message — a skill cannot force that; only a hook
can. If the user wants every message auto-rewritten, tell them that requires a hook
(see the note at the bottom).

## Optimization principles

1. **Decompose vague requests into specifics.** Break "fix everything" into
   enumerated concrete issues; turn "optimize" into measurable criteria; convert
   "review all" into a scoped checklist with clear deliverables.
2. **Add structure.** Separate concerns into subtasks. Mark sequential vs parallel
   work. Use headings, numbered lists for steps, bullets for unordered items.
3. **Eliminate ambiguity.** Say what "done" looks like. State scope boundaries
   (what IS and IS NOT included). Replace vague words ("some", "better", "stuff")
   with specifics.
4. **Add diagnostic depth.** For bugs: name the root-cause questions to ask first.
   For features: constraints and edge cases. For reviews: the criteria to evaluate.
5. **Preserve intent.** Never change WHAT the user is asking for — only improve how
   it's understood and executed. Keep their terminology, examples, and scope. Do
   not expand or shrink the request unless asked.

## Output format

```
## [Clear title describing the task]

### Context (if applicable)
[Why this needs doing / what's broken / the goal]

### Requirements
[Structured, specific, actionable items grouped by concern]

### Scope
[What's included; what flows/areas to cover]

### Expected output
[Numbered list of concrete deliverables]

### Constraints (if applicable)
[Boundaries, limitations, non-goals]
```

After the optimized prompt, add a copy block so the user can grab it:

```
---
Here's your optimized prompt — copy it:

<details>
<summary>Click to copy</summary>

[the full optimized prompt repeated in a fenced code block]

</details>
```

## Example

**Input:**
> optimize: the reel media finder keeps grabbing off-topic clips sometimes, fix it

**Output:**

```markdown
## Fix off-topic clip selection in the reel media finder

### Context
The media finder occasionally selects clips that don't match the topic (e.g. a
generic clip instead of one matching the version number or product named in the
title). Goal: clips must reliably match the topic's key terms.

### Requirements
- Identify where clip candidates are scored and where the wrong one wins
- Check the relevance signal against topic-title keywords (product names,
  version numbers like "4.8", named tools)
- Determine whether off-topic clips are (a) scoring too high, or (b) on-topic
  clips are being filtered out before scoring
- Adjust scoring so topic-keyword matches dominate and off-topic clips sink

### Scope
Included: candidate scoring + relevance logic. Not included: changing the clip
sources or the render pipeline.

### Expected output
1. Root cause identified with file:line references
2. Fix implemented in the scoring logic
3. Verified on a real recent topic where it previously picked wrong
4. Change committed + pushed (ship-automation-change)
```

## Note for the user

To auto-rewrite EVERY message (not just on-demand), you'd need a Claude Code
**hook** on `UserPromptSubmit`, not a skill. Skills only activate when the request
matches this description. On Opus (a large, robust model) the difference between
plain text and structured prompts is small, so on-demand `/optimize` is usually
the better trade-off — you pull it out when you want a rough idea cleaned up.
