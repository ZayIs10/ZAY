/* GenZ Capital — Generic five_beat reel timeline.
 *
 * Animates the structure emitted by publisher/reel_generator.py
 * build_five_beat_html() — three text rows per beat (#b{n}a/b/c) for beats
 * 1–3, then the proof (#b4n/l/s) and CTA (#b5a/b/c).
 *
 * Beat windows (absolute, must match data-start/data-duration in the HTML):
 *   B1 HOOK     0–3s
 *   B2 PROBLEM  3–10s
 *   B3 INSIGHT  10–20s
 *   B4 PROOF    20–27s
 *   B5 CTA      27–30s
 *
 * The neon "punch" lines (#b1b, #b3b, #b4n, #b5b) get scale-pop + glow flicker.
 * The setup/sub lines get a clean fade+slide.
 *
 * The Reel #3 hand-crafted timeline (numbered tool list, etc.) is preserved
 * at reels/archive/script_reel3.js for any future per-reel rendering.
 */

window.__timelines = window.__timelines || {};

const tl = gsap.timeline({ paused: true });

const fadeIn = (sel, at, opts = {}) =>
  tl.fromTo(sel,
    { opacity: 0, y: opts.y ?? 50, filter: `blur(${opts.blur ?? 15}px)` },
    { opacity: 1, y: 0, filter: 'blur(0px)',
      duration: opts.dur ?? 0.50, ease: 'power3.out' }, at);

const punchIn = (sel, at, opts = {}) =>
  tl.fromTo(sel,
    { opacity: 0, scale: 0.3, rotation: opts.rot ?? -4, filter: 'blur(20px)',
      textShadow: '0 0 0 rgba(57,255,20,0)' },
    { opacity: 1, scale: 1.0, rotation: 0, filter: 'blur(0px)',
      textShadow: '0 0 100px rgba(57,255,20,1), 0 0 200px rgba(57,255,20,0.65)',
      duration: opts.dur ?? 0.55, ease: 'back.out(2.4)' }, at);

const flicker = (sel, at, dur = 0.40, repeat = 4) =>
  tl.to(sel, {
    textShadow: '0 0 150px rgba(57,255,20,1), 0 0 300px rgba(57,255,20,0.9)',
    scale: 1.04, duration: dur, ease: 'sine.inOut',
    yoyo: true, repeat
  }, at);

/* ---------- BEAT 1 — HOOK (0–3s) ---------- */
fadeIn('#b1a', 0.05);
punchIn('#b1b', 0.35);
fadeIn('#b1c', 0.95, { y: 30, blur: 10, dur: 0.45 });
flicker('#b1b', 1.30);

/* ---------- BEAT 2 — PROBLEM (3–10s) ---------- */
fadeIn('#b2a', 3.55);
fadeIn('#b2b', 5.30, { y: 40, blur: 12, dur: 0.55 });
fadeIn('#b2c', 7.10, { y: 30, blur: 10, dur: 0.55 });

/* ---------- BEAT 3 — INSIGHT (10–20s) ---------- */
fadeIn('#b3a', 10.55, { y: 60, dur: 0.55 });
punchIn('#b3b', 11.95, { rot: -4, dur: 0.60 });
fadeIn('#b3c', 14.10, { y: 40, blur: 12, dur: 0.55 });
flicker('#b3b', 15.50, 0.45, 5);

/* ---------- BEAT 4 — PROOF (20–27s) ---------- */
tl.fromTo('#b4n',
  { opacity: 0, scale: 0.18, rotation: -8, filter: 'blur(25px)',
    textShadow: '0 0 0 rgba(57,255,20,0)' },
  { opacity: 1, scale: 1.0, rotation: 0, filter: 'blur(0px)',
    textShadow: '0 0 120px rgba(57,255,20,1), 0 0 240px rgba(57,255,20,0.75)',
    duration: 0.70, ease: 'back.out(3)' }, 20.55);

tl.to('#b4n',
  { x: 8, duration: 0.05, ease: 'none', yoyo: true, repeat: 6 }, 21.30);

fadeIn('#b4l', 21.40, { y: 50, blur: 10, dur: 0.55 });
tl.fromTo('#b4s',
  { opacity: 0, y: 25, letterSpacing: '0.20em' },
  { opacity: 1, y: 0, letterSpacing: '0.10em',
    duration: 0.55, ease: 'power2.out' }, 23.95);

tl.to('#b4n', {
  textShadow: '0 0 170px rgba(57,255,20,1), 0 0 340px rgba(57,255,20,0.95)',
  scale: 1.04, duration: 0.45, ease: 'power2.inOut',
  yoyo: true, repeat: 6
}, 22.30);

/* ---------- BEAT 5 — CTA (27–30s) ---------- */
tl.fromTo('#b5a',
  { opacity: 0, y: 60, filter: 'blur(20px)', letterSpacing: '0.20em' },
  { opacity: 1, y: 0, filter: 'blur(0px)', letterSpacing: '0.02em',
    duration: 0.50, ease: 'power3.out' }, 27.55);

tl.fromTo('#b5b',
  { opacity: 0, scale: 0.5, filter: 'blur(15px)',
    textShadow: '0 0 0 rgba(57,255,20,0)' },
  { opacity: 1, scale: 1.0, filter: 'blur(0px)',
    textShadow: '0 0 100px rgba(57,255,20,1), 0 0 200px rgba(57,255,20,0.65)',
    duration: 0.55, ease: 'back.out(2.4)' }, 28.05);

tl.fromTo('#b5c',
  { opacity: 0, y: 28, letterSpacing: '0.20em' },
  { opacity: 1, y: 0, letterSpacing: '0.08em',
    duration: 0.45, ease: 'power2.out' }, 28.65);

tl.to('#b5b', {
  textShadow: '0 0 140px rgba(57,255,20,1), 0 0 280px rgba(57,255,20,0.95)',
  scale: 1.05, duration: 0.30, ease: 'power2.inOut',
  yoyo: true, repeat: 3
}, 28.85);

window.__timelines['main'] = tl;

/* ---------- AUTO-FIT — guarantee every text line fits the 1080px frame ----------
 * The brand font sizes are large and fixed; a long headline word would
 * otherwise overflow the stage edges. fitReelText() measures each text line
 * and shrinks ONLY its font-size until it fits inside its container's safe
 * width. It is deterministic (pure DOM measurement — no random, no clock, no
 * network) so it is safe for HyperFrames frame capture. GSAP animates
 * opacity/scale/transform/textShadow only, never font-size, so this never
 * fights the timeline.
 *
 * Runs once synchronously (conservative fallback-font metrics) and again on
 * document.fonts.ready (accurate Anton metrics) — the second pass resets each
 * line to its CSS base size first, so the final fit is tight, not over-shrunk.
 */
function fitReelText() {
  var STAGE_W = 1080;
  var MIN_FS = 28;            // never shrink below this (px)
  var SAFETY = 14;            // px breathing room inside the container
  var selectors = [
    '.line-setup', '.line-punch', '.line-sub',
    '.proof-big', '.proof-lbl', '.proof-sub',
    '.cta-top', '.cta-mid', '.cta-foot'
  ];
  var nodes = document.querySelectorAll(selectors.join(','));
  for (var i = 0; i < nodes.length; i++) {
    var el = nodes[i];
    if (!el.dataset.baseFs) {
      el.dataset.baseFs = window.getComputedStyle(el).fontSize;
    }
    el.style.whiteSpace = 'nowrap';        // controlled line breaks only
    el.style.fontSize = el.dataset.baseFs; // reset before (re-)fitting

    var parent = el.parentElement;
    var padX = 0;
    if (parent) {
      var pcs = window.getComputedStyle(parent);
      padX = parseFloat(pcs.paddingLeft || 0) + parseFloat(pcs.paddingRight || 0);
    }
    var avail = STAGE_W - padX - SAFETY;

    var fs = parseFloat(el.dataset.baseFs);
    var guard = 0;
    while (el.scrollWidth > avail && fs > MIN_FS && guard < 160) {
      fs *= 0.97;
      el.style.fontSize = fs + 'px';
      guard++;
    }
  }
}

if (document.fonts && document.fonts.ready) {
  document.fonts.ready.then(fitReelText);
}
fitReelText();
