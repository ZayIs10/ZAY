# Workflow: Research Topic

## Objective
Transform one Google Sheets topic row into a validated content brief for a Gen Z Capital Instagram post.

## Required Inputs
- `topic` (string)
- `key_points` (string or list)
- `brand_tone` (string)
- Source row identifier (`row_id` or timestamp)

## Tools To Use
- Existing Google Sheets read tool/script (preferred)
- `research.py` for topic expansion and signal gathering — uses **free sources only** (no Apify, no Serper)
- Free sources: Reddit public JSON, HackerNews Algolia API, finance RSS feeds, Google Trends (pytrends), NewsAPI.org free tier

## Steps
1. Read the next unpublished row from the sheet.
2. Validate required fields (`topic`, `key_points`, `brand_tone`); if missing, stop and request clarification.
3. Run research to extract:
   - 3-5 key insights
   - 1 clear angle (problem/opportunity/risk)
   - 1 numeric hook if available
4. Normalize output into a short brief with:
   - Headline direction
   - Supporting bullets
   - Source confidence notes
5. Save intermediate outputs to `.tmp/` only.

## Expected Outputs
- Structured brief object for image/caption generation
- Status flag for next workflow stage

## Edge Cases
- No unpublished rows: exit cleanly with "no work".
- Weak or low-confidence research: mark as `needs_review` and do not publish.
- Rate limit/API failure: each source fails silently and pipeline continues with remaining sources. If ALL sources return 0 items, check network connectivity and wait 60 minutes before retry (Google Trends throttles aggressively).
- Google Trends `TooManyRequestsError`: skip silently — Reddit + HN + RSS are sufficient fallback.

