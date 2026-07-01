/* GenZ Capital — Reel #2 v2: The Algorithm Decoded.
 * 30s, 9:16. Camera lives inside an Instagram-post mockup; when the VO
 * names a metric ("likes" / "DM" / "shares" / "saves"), the mockup +
 * underlying post-clip videos transform together so the relevant icon
 * fills the screen.
 *
 * Audio: assets/audio/reel2_voiceover.mp3 (track-index 5, mux'd manually).
 *
 * Scene timings (absolute):
 *   S1 HOOK     0.0  – 3.0   "Instagram doesn't count likes anymore."
 *   S2 PROBLEM  3.0  – 10.0  "Sends per reach... how many people DM..."
 *   S3 INSIGHT 10.0  – 20.0  "Shares first. Watch time second. Saves third."
 *   S4 PROOF   20.0  – 27.0  "1% sends per reach"
 *   S5 CTA     27.0  – 30.0  "Comment 'next'..."
 */

window.__timelines = window.__timelines || {};

const CAMERA = ['#igMockup', '.post-clip'];

/* Icon centers in canvas coords (1080x1920).
 * Action bar lives at y=1390..1520, vertical center y=1455.
 * Bookmark is right-aligned. */
const ICON = {
  heart:    { x: 72,   y: 1455 },
  share:    { x: 324,  y: 1455 },
  bookmark: { x: 1008, y: 1455 },
};
const ZOOM = 5;

/* Convert (ix, iy, scale) → translate that centers icon at canvas (540,960). */
function camTo(icon, s) {
  return { x: 540 - s * icon.x, y: 960 - s * icon.y, scale: s };
}
const cam = {
  reset: { x: 0, y: 0, scale: 1 },
  heart: camTo(ICON.heart, ZOOM),
  share: camTo(ICON.share, ZOOM),
  book:  camTo(ICON.bookmark, ZOOM),
};

/* Anchor camera transforms at top-left so our translate math is exact. */
gsap.set(CAMERA, { transformOrigin: '0 0', x: 0, y: 0, scale: 1 });

const tl = gsap.timeline({ paused: true });

/* ==================== SCENE 1 — HOOK (0–3s) ====================
 * "Instagram doesn't count likes anymore." */

// Subtitle in (lands ~0.45s before VO)
tl.fromTo('#s1a',
  { opacity: 0, y: 50, filter: 'blur(15px)' },
  { opacity: 1, y: 0, filter: 'blur(0px)', duration: 0.40, ease: 'power3.out' }, 0.05);
tl.fromTo('#s1b',
  { opacity: 0, scale: 0.4, filter: 'blur(20px)' },
  { opacity: 1, scale: 1.0, filter: 'blur(0px)', duration: 0.55, ease: 'back.out(2.4)' }, 0.40);

// 1.4s: camera punches into the heart icon as VO says "likes"
tl.to(CAMERA, { ...cam.heart, duration: 0.55, ease: 'power3.in' }, 1.30);
// 1.7s: heart briefly turns red (alive)
tl.call(() => document.getElementById('iconHeart').classList.add('heart-on'), null, 1.75);
// 2.05s: heart dies — turns gray
tl.call(() => {
  const h = document.getElementById('iconHeart');
  h.classList.remove('heart-on');
  h.classList.add('heart-dead');
}, null, 2.05);
// 2.10s: red strikethrough draws across the dead heart
tl.fromTo('#iconStrike',
  { scaleX: 0 },
  { scaleX: 1, duration: 0.30, ease: 'power2.out' }, 2.10);
// 2.45s: pull back to neutral framing for scene 2 hand-off
tl.to(CAMERA, { ...cam.reset, duration: 0.50, ease: 'power3.out' }, 2.45);

/* ==================== SCENE 2 — PROBLEM (3–10s) ====================
 * "The strongest signal in the 2026 algorithm is sends per reach.
 *  How many people DM your post to a friend.
 *  Likes are the weakest signal in the entire system."
 *
 * Estimated word landings:
 *   "DM"     ~ 6.0
 *   "likes"  ~ 7.7  (heart shrivels)
 */

tl.fromTo('#s2a',
  { opacity: 0, y: 50, filter: 'blur(15px)' },
  { opacity: 1, y: 0, filter: 'blur(0px)', duration: 0.50, ease: 'power3.out' }, 3.05);
tl.fromTo('#s2b',
  { opacity: 0, scale: 0.4, rotation: -3, filter: 'blur(20px)' },
  { opacity: 1, scale: 1.0, rotation: 0, filter: 'blur(0px)',
    duration: 0.60, ease: 'back.out(2.6)' }, 3.45);

// 5.7s: zoom into paper-plane on "DM"
tl.to(CAMERA, { ...cam.share, duration: 0.55, ease: 'power3.in' }, 5.65);
tl.call(() => document.getElementById('iconShare').classList.add('share-glow'), null, 5.95);

// Send-counter ticks 0 → 47 while zoomed in (proxy for "DM your post")
tl.to({ v: 0 }, {
  v: 47, duration: 1.10, ease: 'power2.out',
  onUpdate: function () {
    const el = document.getElementById('igShareCount');
    if (el) el.textContent = String(Math.round(this.targets()[0].v));
  }
}, 5.95);

// 7.05s: pull back so viewer sees both the live paper-plane AND the dead heart
tl.to(CAMERA, { ...cam.reset, duration: 0.55, ease: 'power3.out' }, 7.05);

// 7.7s: "Likes are the weakest" — dead heart visibly shrivels in the corner
tl.to('#iconHeart',
  { scale: 0.55, opacity: 0.45, duration: 0.45, ease: 'power2.in' }, 7.65);
tl.to('#igLikes',
  { color: '#707070', duration: 0.4 }, 7.65);

/* ==================== SCENE 3 — INSIGHT (10–20s) ====================
 * "Shares first. Watch time second. Saves third.
 *  None of these accounts chase likes. They engineer DMs."
 *
 * Lines land ~3.3s apart per the script. */

// Slight chrome dim during scene 3 so the rank list reads on top
tl.to('#igMockup', { filter: 'brightness(0.65)', duration: 0.35 }, 9.95);

/* --- "1. SHARES" (~10.0s) --- */
tl.fromTo('#r1',
  { opacity: 0, x: -200, filter: 'blur(15px)' },
  { opacity: 1, x: 0, filter: 'blur(0px)', duration: 0.55, ease: 'power3.out' }, 9.95);
// camera punches into share icon
tl.to(CAMERA, { ...cam.share, duration: 0.45, ease: 'power3.in' }, 10.20);
// neon green glow re-pulses on the paper-plane
tl.fromTo('#iconShare',
  { scale: 1, filter: 'drop-shadow(0 0 28px rgba(57,255,20,0.95))' },
  { scale: 1.12, filter: 'drop-shadow(0 0 60px rgba(57,255,20,1))',
    duration: 0.35, ease: 'sine.inOut', yoyo: true, repeat: 1 }, 10.50);
tl.to(CAMERA, { ...cam.reset, duration: 0.50, ease: 'power3.out' }, 11.20);

/* --- "2. WATCH TIME" (~13.3s) — no IG icon, so just highlight the post body --- */
tl.fromTo('#r2',
  { opacity: 0, x: -200, filter: 'blur(15px)' },
  { opacity: 1, x: 0, filter: 'blur(0px)', duration: 0.55, ease: 'power3.out' }, 13.20);
// quick subtle push into the post image (~zoom 1.2x toward image center y=850)
tl.to(CAMERA,
  { x: 540 - 1.2 * 540, y: 960 - 1.2 * 850, scale: 1.2,
    duration: 0.40, ease: 'power2.in' }, 13.40);
tl.to(CAMERA, { ...cam.reset, duration: 0.55, ease: 'power3.out' }, 14.30);

/* --- "3. SAVES" (~16.6s) — push into the bookmark, fill it neon --- */
tl.fromTo('#r3',
  { opacity: 0, x: -200, filter: 'blur(15px)' },
  { opacity: 1, x: 0, filter: 'blur(0px)', duration: 0.55, ease: 'power3.out' }, 16.55);
tl.to(CAMERA, { ...cam.book, duration: 0.45, ease: 'power3.in' }, 16.80);
tl.call(() => document.getElementById('iconBookmark').classList.add('bookmark-on'), null, 17.05);
tl.fromTo('#iconBookmark',
  { scale: 1 },
  { scale: 1.15, duration: 0.30, ease: 'sine.inOut', yoyo: true, repeat: 1 }, 17.10);
tl.to(CAMERA, { ...cam.reset, duration: 0.50, ease: 'power3.out' }, 17.80);

/* --- "they engineer DMs" (~18.7s) — final zoom to paper-plane --- */
tl.to(CAMERA, { ...cam.share, duration: 0.45, ease: 'power3.in' }, 18.55);
tl.fromTo('#iconShare',
  { scale: 1, filter: 'drop-shadow(0 0 28px rgba(57,255,20,0.95))' },
  { scale: 1.18, filter: 'drop-shadow(0 0 80px rgba(57,255,20,1))',
    duration: 0.30, ease: 'sine.inOut', yoyo: true, repeat: 2 }, 18.85);
tl.to(CAMERA, { ...cam.reset, duration: 0.50, ease: 'power3.out' }, 19.40);

// Mockup fades out before scene 4
tl.to('#igMockup', { opacity: 0, duration: 0.40, ease: 'power2.in' }, 19.55);

/* ==================== SCENE 4 — PROOF (20–27s) ====================
 * "Hit one percent sends per reach, and the algorithm pushes your reel
 *  to people who don't follow you. That's how zero-follower accounts go viral." */

// Massive 0% → 1% count-up
tl.fromTo('#proofNum',
  { opacity: 0, scale: 0.18, filter: 'blur(25px)' },
  { opacity: 1, scale: 1.0, filter: 'blur(0px)',
    duration: 0.70, ease: 'back.out(2.8)' }, 20.10);
tl.to({ v: 0 }, {
  v: 1, duration: 1.40, ease: 'power2.out',
  onUpdate: function () {
    const el = document.getElementById('proofNum');
    if (el) el.textContent = Math.round(this.targets()[0].v) + '%';
  }
}, 20.55);

// Sustained neon flicker once it hits 1%
tl.to('#proofNum', {
  textShadow: '0 0 170px rgba(57,255,20,1), 0 0 340px rgba(57,255,20,0.95)',
  scale: 1.04, duration: 0.40, ease: 'sine.inOut',
  yoyo: true, repeat: 6
}, 22.00);

// Sub-labels
tl.fromTo('#proofLbl',
  { opacity: 0, y: 50, filter: 'blur(10px)' },
  { opacity: 1, y: 0, filter: 'blur(0px)', duration: 0.55, ease: 'power3.out' }, 22.10);
tl.fromTo('#proofSub',
  { opacity: 0, y: 25, letterSpacing: '0.20em' },
  { opacity: 1, y: 0, letterSpacing: '0.10em', duration: 0.55, ease: 'power2.out' }, 23.85);

/* ==================== SCENE 5 — CTA (27–30s) ====================
 * "Comment 'next' for the four content types..." */

// 6 profile circles fade-up with stagger
tl.fromTo('.r2-cta-grid img',
  { opacity: 0, scale: 0.6, filter: 'blur(12px)' },
  { opacity: 1, scale: 1.0, filter: 'blur(0px)',
    duration: 0.50, ease: 'back.out(2.2)', stagger: 0.06 }, 27.05);

tl.fromTo('#ctaA',
  { opacity: 0, y: 60, filter: 'blur(20px)', letterSpacing: '0.20em' },
  { opacity: 1, y: 0, filter: 'blur(0px)', letterSpacing: '0.02em',
    duration: 0.50, ease: 'power3.out' }, 27.55);

// Neon divider expands horizontally
tl.fromTo('#ctaDiv',
  { width: 0 },
  { width: 720, duration: 0.55, ease: 'power3.out' }, 28.00);

tl.fromTo('#ctaB',
  { opacity: 0, scale: 0.5, filter: 'blur(15px)' },
  { opacity: 1, scale: 1.0, filter: 'blur(0px)',
    duration: 0.55, ease: 'back.out(2.4)' }, 28.05);

tl.fromTo('#ctaC',
  { opacity: 0, y: 28, letterSpacing: '0.20em' },
  { opacity: 1, y: 0, letterSpacing: '0.08em',
    duration: 0.45, ease: 'power2.out' }, 28.65);

tl.to('#ctaB', {
  textShadow: '0 0 80px rgba(57,255,20,0.95), 0 0 160px rgba(57,255,20,0.55)',
  scale: 1.04, duration: 0.30, ease: 'sine.inOut',
  yoyo: true, repeat: 3
}, 28.85);

window.__timelines['main'] = tl;
