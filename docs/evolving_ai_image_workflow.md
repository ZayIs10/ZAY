# How @evolving.ai-Style Pages Make Their Images (and how WE copy it)

Research compiled 2026-06-06. This documents the real workflow faceless AI-news
pages use to make those clean, cinematic, on-brand images — and the exact
recipe for Gen Z Capital to reproduce it.

---

## The key insight (the user was right)

They do **NOT** type a prompt cold and hope. The pros work **image-first**:

> Find a real reference image → let an AI *see* it (vision) → AI writes the
> precise prompt OR edits the image directly → generate a clean, branded,
> cinematic version.

This is called **reverse-prompt engineering** (a.k.a. image-to-prompt) plus
**reference-image / image-to-image** generation. It's why their images all feel
like the same "brand" and never look like random stock generations.

---

## The 5 ways they actually get a great image (ranked by how evolving.ai works)

### Method A — Reference → Vision → Prompt → Generate  ⭐ (the user's hypothesis)
1. Grab a real photo tied to the news (e.g. the actual product screenshot, the
   robot, the lab photo, a press image).
2. Upload it to ChatGPT/GPT vision and ask:
   *"Describe this image in enough detail that a text-to-image AI could recreate
   it. Focus on subject, lighting, color palette, composition, mood, lens."*
3. ChatGPT returns a rich prompt. You tweak it ("make it darker, add neon-green
   accent, no text").
4. Paste that prompt into the image model → clean recreated version you own.
*Why it works:* the look is anchored to something real, so it never feels
generic, and the news image's composition carries over.

### Method B — Image-to-image / edit directly (no re-typing)
- Upload the reference image straight into GPT Image 2 / Nano Banana Pro and
  say *"recreate this in dark cinematic style, teal-black grade, neon-green
  accent, vertical 4:5, leave clean space at top, no text."*
- The model keeps the composition and just restyles it. Fastest path.

### Method C — Style Reference (lock the LOOK across all slides)
- Feed the model ONE of your own already-good slides as a **style reference**
  (Midjourney `--sref`, Firefly Style Reference, Nano Banana identity-lock).
- Every new image inherits the same grade/lighting → the whole feed looks like
  one brand. This is the real secret to evolving.ai's consistency.

### Method D — Cinematic enhancer on a plain image
- Take any flat photo, run it through an image-to-image "cinematic" pass
  (color grade, depth of field, film lighting). Good for upgrading screenshots.

### Method E — Pure text-to-image with a locked STYLE BLOCK
- What we already do in `claude_writing_prompt_PLAN.md`: a fixed style block
  appended to every prompt. Works, but Methods A–C look more premium because
  they're anchored to a real reference.

---

## Best models in 2026 (for this exact use case)

| Model | Best at | Reference support | Notes |
|---|---|---|---|
| **GPT Image 2** | Photorealism, character/scene consistency from a reference, respecting prompt intent | up to ~16 source images | Best all-round for our cinematic slides. Wins most blind tests. |
| **Nano Banana Pro** | Text rendering, infographics, identity-lock across many assets | up to **14 reference images** | Best when you need the SAME look across a whole carousel. |
| **Midjourney** | High-end stylized aesthetics | Style Ref (`--sref`) + Omni Ref | Gorgeous but needs tuning. |
| **Adobe Firefly** | Brand-safe, commercial-clear, Style + Composition Reference | yes | Safest legally for a brand. |
| **Leonardo AI** | Strong reference controls, concept art | Character/Content/Style Ref | Good free-ish option. |

For Gen Z Capital: **GPT Image 2** for hero slides, **Nano Banana Pro** when you
need 8 slides to look identical. Both render text cleanly (~80% less manual
cleanup).

---

## THE GEN Z CAPITAL RECIPE (do this every carousel)

**Step 1 — Source a real reference per slide.**
From the news itself: product screenshot, company press image, a relevant Pexels
/ Unsplash photo, or a frame from the source company's video. (Keep our
copyright rules: own screen-recs, Pexels/Unsplash, or credited + transformed.)

**Step 2 — Vision-to-prompt (Method A).** Paste this into GPT with the image:
> "You are an art director for a dark cinematic AI brand. Look at this image and
> write a single text-to-image prompt that recreates its SUBJECT and COMPOSITION
> but in our house style: ultra-realistic, dark moody lighting, shallow depth of
> field, teal-and-black color grade with subtle neon-green (#39FF14) accents,
> vertical 4:5, main subject in the lower two-thirds, clean darker space at the
> top for a headline, absolutely no text/words/letters/logos. Return only the
> prompt."

**Step 3 — Lock the look (Method C).** After your first good image, feed it back
as a **style reference** for every other slide so all 8 match.

**Step 4 — Generate at 1080×1350 (4:5)**, download, and drop into
`assets/images/generated/` named `claude_write_1.png` … so
`carousel_format.py` picks them up automatically.

**Step 5 — Render** with `python publisher/carousel_format.py --spec <spec.json>`.
Our engine adds the headline, neon accent, brand line, dots — so the images must
stay TEXT-FREE (that's why every prompt ends in "no text").

---

## One-line summary
Real reference image → GPT vision writes the house-style prompt → generate /
restyle → reuse the first result as a style reference to lock all 8 slides →
render headlines on top with our engine. That's the evolving.ai look, owned.

Sources: MindStudio (reverse-engineer image prompts), Adobe Firefly style/
composition reference docs, getimg.ai & Atlas Cloud 2026 model benchmarks
(GPT Image 2 vs Nano Banana Pro), neolemon reference-image generator roundup.
