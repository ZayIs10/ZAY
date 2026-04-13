# Workflow: Generate Post Asset

## Objective
Generate one branded 1080x1350 Instagram image and one caption from a validated brief.

## Required Inputs
- Structured research brief from `workflows/research_topic.md`
- Brand rules from `gen_z_post_guidelines.md` and `research_config.json`
- Logo asset (`example_image.png` or configured logo)

## Tools To Use
- `post_generator.py` (primary deterministic renderer)
- Config loader from `research_config.json`
- Caption generation logic (existing OpenAI path)

## Steps
1. Validate that all visual constraints are available:
   - 1080x1350 canvas
   - Top 60% cinematic image, bottom 40% black text zone
   - Correct color mapping (white / neon green / gray)
   - Logo placement at top-center
2. Generate or fetch background image based on topic.
3. Build 3-line headline + short subheadline in brand tone.
4. Render final post image with readability overlay and spacing rules.
5. Generate matching Instagram caption (no emojis, direct tone).
6. Save intermediate files to `.tmp/`; save final image as publish-ready artifact.

## Expected Outputs
- Final image path
- Final caption text
- Minimal metadata (topic, generated_at, model/tool used)

## Edge Cases
- Missing logo/font/config: stop and request manual fix.
- Text overflow: auto-shrink/reflow once; if still failing, mark `needs_review`.
- Image API failure: retry with fallback source or safe placeholder and flag review.

