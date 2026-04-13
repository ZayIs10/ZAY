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

