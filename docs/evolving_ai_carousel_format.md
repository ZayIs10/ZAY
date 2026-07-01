# @evolving.ai Carousel Format — Locked Spec for Gen Z Capital

Last updated: 2026-06-18. Built from **real @evolving.ai slides** (10 covers +
one complete multi-slide post pulled logged-in, saved under
`.tmp/evolvingai_live/` and `.tmp/evolvingai/`) cross-checked against our actual
renderer (`publisher/carousel_format.py`). This is the format every Gen Z Capital
carousel should follow.

> **Why we copy them:** @evolving.ai is the proven template for AI-news/AI-tool
> carousels — dark, cinematic, one dramatic image per slide, instantly readable
> at thumbnail size. We copy their *look and structure*, but bend the *content*
> to our niche (teaching a usable AI skill — "no fluff, no hype"), because they
> mostly REPORT news and we mostly TEACH. See the "Where we differ" section.

---

## 0. TUTORIAL IS THE LOCKED DEFAULT — use it for EVERY carousel (user lock 2026-06-27)

The user approved a specific deck and said: *"save this format so whenever I ask
you to create a carousel post you would use this format."* So **tutorial is the
default for every carousel**, in code AND in how the assistant builds one:

- `choose_format()` returns **tutorial** for any topic that is not an
  unmistakable numbered round-up (→ listicle) or pure breaking news with no
  how-to angle (→ news_hybrid). A news-y topic gets *reframed as a how-to*, not
  reported. Override only with an explicit `--format` / Sheet `Format` column.
- **The golden reference deck** (the look the user signed off on):
  `assets/carousels/build_an_app_with_no_code_using_claude_c/` — topic
  *"Build an App With No Code Using Claude Code."* Slide order, hook style, neon
  key word, numbered chips, recap, and CTA in that deck = the bar every future
  carousel must hit. Its spec is the worked example of this whole doc.
- **The locked slide order (never reorder, never drop recap):**
  `cover ("outcome in X steps") → STEP 1..N (one action each) → PRO TIP →
  recap (the SAVE engine, REQUIRED) → CTA (specific value, never bare "follow")`.
- **Content must be TRUE.** Real steps for the real tool (the Claude-Code deck
  was corrected from an invented click-UI to the real CLI flow before render).
  No invented buttons, menus, stats, or quotes — this is the teach-a-skill brand.

This block is the contract. The slide-by-slide detail below is how to satisfy it.

### Delivery rule — REVIEW ONLY, never auto-post to Instagram (user lock 2026-06-27)

Every carousel the assistant builds STOPS at review. The pipeline's last step is
the review gate (`carousel_review.review_gate`), which does exactly three things
and nothing more:

1. **Render** the slide PNGs.
2. **Upload the deck folder to Google Drive** and return ONE shareable folder
   link (every slide + `caption.txt` inside).
3. **Email** the user that Drive link + the copy-paste-ready caption, and mark
   the Sheet row **"Ready to Post."**

It does **NOT** post to Instagram, does NOT create an IG draft/container, and
makes NO Graph-API call — there is intentionally no publish code in the carousel
path. Instagram only ever happens later, manually, when the USER flips the row to
**"Approved to Post."** When a build runs locally (where the Gmail app password
isn't available), fire `.github/workflows/send_carousel_review.yml` to send the
same review email from the cloud. Drive auth = the user's OAuth token
(`google_drive_token.json`); never the service account (no storage quota).

---

## 1. The fixed canvas (non-negotiable)

| Property | Value | Where |
|---|---|---|
| Ratio | **1080 × 1350** (4:5 portrait) | `W, H` in carousel_format.py |
| Base color | near-black `(8,8,9)` / black | the feed reads as one dark block |
| Accent | neon-green **#39FF14** `(57,255,20)` | `NEON` — our signature vs their white |
| Side margin | **70 px** | `MARGIN` |
| Headline font | Anton / Archivo Black (heavy, ALL-CAPS) | scroll-stopping weight |
| Body font | Inter | calm, readable |

---

## 2. The 3 things on EVERY slide (their consistency engine)

This is what makes a feed look like ONE account, not random posts:

1. **The brand wordmark** — `EVOLVING AI` for them; **GenZ Capital** for us
   (neon-bar lockup on the cover, top-corner on content slides).
2. **The story's PRODUCT logo** — the company/product the slide is about
   (Claude starburst, ChatGPT mark, Gemini, …). PRODUCT logo, NOT the parent
   company. Auto-fetched free (`source_logo.py`). This is THE @evolving.ai move
   — their ChatGPT post shows the ChatGPT logo glowing in Sam Altman's hands.
3. **One dominant focal point** — exactly one subject per slide. Cover = dramatic
   art; content = ONE real screenshot/clip. 3+ competing elements kills CTR.

---

## 3. Slide-by-slide anatomy

### Slide 1 — COVER (the hook; 70% of the win is here)
- A **big dramatic image** filling the top ~60% (`SPLIT = int(H*0.60)`).
- `GenZ Capital` neon-bar lockup (neon dividers L+R of the wordmark).
- **Huge ALL-CAPS headline**, auto-shrunk to fit the bottom 40% band — it can
  never creep up and cover the image (the 60/40 rule, see
  `feedback_cover_image_must_match_topic` memory).
- One neon-green **power word** inside the headline (e.g. `RISKY`).
- Bottom: a one-line gray subheadline OR **`SWIPE FOR MORE`** (`swipe_y = H-120`).
- The cover image is NOT one rigid formula — it VARIES per topic. From the 10
  real covers: a film-set BTS shot, a CEO holding a glowing product logo, a
  cinematic silhouette, a giant CGI creature. **Variety is the strategy.** Our
  `cover_director.py` already picks 1 of 6 archetypes per topic to match this.

### Slides 2…N — CONTENT (the payload)
Real @evolving.ai content slides have:
- A **media card**: the slide's image/clip in a large **rounded-corner card**
  (~28px side margin, ~28px radius) — NOT edge-to-edge. Real footage/screenshot.
- Dark **diagonal corner slashes** top-left + bottom-right (their signature frame).
- A **small numbered label top-left** when it's a list (`2. Moana`,
  `5. Steven Universe`) — this is how they do listicles.
- A short title/caption above or on the card.
- On content slides the media is REAL (screen-rec, screenshot, source clip),
  never AI-generated — AI art is for the cover only.

### Last slide — CTA / FOLLOW
- Same card layout.
- The **`@handle` profile chip top-right** (their `@evolving.ai` badge) +
  a **follow** cue.
- Ours: "Follow for daily AI tools & how-tos. No fluff. No hype." + 3 pills.

---

## 4. Slide count is NOT fixed — the STORY decides

This is the single most-misunderstood thing. @evolving.ai posts range from **3 to
12+ slides**:

| Story type | Slides | Why |
|---|---|---|
| Tight news drop | **3–5** | hook → the claim → what it means |
| Listicle ("7 examples / 5 tools") | **1 per item** + cover + CTA | each slide = item #N |
| Deep how-to | **6–9** | one step per slide |

**Rule: one idea = one slide. Never pad to hit a number, never cram two ideas on
one slide.** Their highest-reach posts are **numbered listicles** where each
slide is item N — easy to make, easy to swipe, high saves.

---

## 5. Where WE differ from @evolving.ai (don't copy blindly)

| | @evolving.ai | Gen Z Capital |
|---|---|---|
| Core job | **Report** AI news ("look what dropped") | **Teach** a usable AI skill |
| Best format | News carousel | **Numbered tutorial listicle** (their listicle skeleton, our how-to content) |
| Accent | White text | **Neon-green #39FF14** |
| Voice | Hype-adjacent | "No fluff. No hype." |
| Last slide | Follow | Follow + a saveable takeaway |

**Our highest-performing format = the tutorial listicle:**
> Cover (dramatic hook) → "the problem" → numbered steps/tools (1 per slide) →
> recap → CTA/follow.

Our renderer (`carousel_format.py` + `carousel_templates.py`) already produces
this skeleton. The three locked templates are **tutorial (#1) / listicle (#2) /
news_hybrid (#3)** — default new how-to posts to tutorial or listicle, reserve
news_hybrid for actual news.

---

## 6. The cover-image archetypes (matching their variety)

`publisher/cover_director.py` auto-picks ONE per topic so covers stop the scroll
AND match the story (full detail in the `cover_director_archetypes` memory):

| Archetype | When | Look |
|---|---|---|
| staged_dramatic_scene | danger / future / dread | lone figure dwarfed by glowing AI scale (RED accent if danger) |
| ceo_face_reaction | a real person is the actor | recognizable CEO, focused expression, rim light |
| product_screenshot_hero | tutorial / tool how-to | glowing device + a hand entering frame |
| before_after_split | vs / comparison / Nx | one split image, old (dim) vs new (lit) |
| money_number_hero | funding / valuation / layoffs | vault / cash / rising chart-as-light |
| symbolic_object_hero | abstract concept (default) | one glowing symbolic object on near-black |

Universal cover rules: subject in TOP 58%, clean near-black bottom 40% for the
headline, ONE focal point, near-black + one accent rim, **zero text/logo in the
generated image** (the renderer overlays all text + logos).

---

## 7. Pre-publish checklist (every carousel)

- [ ] Cover image VARIES from the last post (not the same archetype back-to-back)
- [ ] Cover headline fits the bottom 40% — image not covered
- [ ] The **product** logo shows (Claude starburst, not the Anthropic "A")
- [ ] One idea per slide; slide count matches the story (no padding)
- [ ] Content slides use REAL media (screenshot/clip), not AI art
- [ ] Last slide = follow CTA + a saveable takeaway
- [ ] Caption: hook → summary → bullets → CTA → hashtags
- [ ] Runs through the review gate (Drive + email), never auto-posts blind

---

## References
- Real slides: `.tmp/evolvingai_live/` (10 covers), `.tmp/evolvingai/` (full post)
- Renderer: `publisher/carousel_format.py`
- Templates: `publisher/carousel_templates.py`
- Cover director: `publisher/cover_director.py`
- Logos: `publisher/source_logo.py`
- Memories: `reference_evolving_ai_format`, `cover_director_archetypes`,
  `source_company_logo`, `carousel_format_templates`, `feedback_cover_image_must_match_topic`
