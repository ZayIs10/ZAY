/* GenZ Capital — Reel #4: The 4 Secrets (payoff to Reel 1's cliffhanger)
 * 30s, 9:16, 6 beats. Hard cuts. Each text reveal lands ~0.4s before its VO.
 * Audio: assets/audio/reel4_voiceover.mp3 (track-index 2).
 *
 * Beat timings (absolute):
 *   B1 HOOK     0.0  – 4.0   (flash montage 0–2.2s, punch 2.2–4.0; VO @ 0.30)
 *   B2 SECRET#1 4.0  – 10.0  (VO @ 4.30)
 *   B3 SECRET#2 10.0 – 16.0  (VO @ 10.30)
 *   B4 SECRET#3 16.0 – 22.0  (VO @ 16.30)
 *   B5 SECRET#4 22.0 – 27.0  (VO @ 22.30)
 *   B6 CTA      27.0 – 30.0  (VO @ 27.30)
 */

window.__timelines = window.__timelines || {};

const tl = gsap.timeline({ paused: true });

/* ---------- BEAT 1 — HOOK (0–4s) ----------
 * 4 profile flashes (CSS visibility handles the cuts) → black with punch.
 */

// Punch text — both lines hit hard at 2.0 when we cut to black
tl.fromTo('#b1a',
  { opacity: 0, y: 60, filter: 'blur(20px)', letterSpacing: '0.20em' },
  { opacity: 1, y: 0, filter: 'blur(0px)', letterSpacing: '0.02em',
    duration: 0.45, ease: 'power3.out' }, 2.00);

tl.fromTo('#b1b',
  { opacity: 0, scale: 0.3, rotation: -6, filter: 'blur(20px)',
    textShadow: '0 0 0 rgba(57,255,20,0)' },
  { opacity: 1, scale: 1.0, rotation: 0, filter: 'blur(0px)',
    textShadow: '0 0 120px rgba(57,255,20,1), 0 0 240px rgba(57,255,20,0.7)',
    duration: 0.55, ease: 'back.out(2.6)' }, 2.35);

// sustained neon flicker on "4 SECRETS" through the rest of the beat
tl.to('#b1b', {
  textShadow: '0 0 160px rgba(57,255,20,1), 0 0 320px rgba(57,255,20,0.95)',
  scale: 1.05, duration: 0.30, ease: 'sine.inOut',
  yoyo: true, repeat: 5
}, 2.90);

/* ---------- BEAT 2 — SECRET #1 (4–10s) — NEVER SHOW THEIR FACE ----------
 * VO @ 4.30. Text in by 4.20.
 */
tl.fromTo('#b2n',
  { opacity: 0, scale: 0.4, filter: 'blur(15px)' },
  { opacity: 1, scale: 1.0, filter: 'blur(0px)',
    textShadow: '0 0 110px rgba(57,255,20,1), 0 0 220px rgba(57,255,20,0.7)',
    duration: 0.55, ease: 'back.out(2.6)' }, 4.05);

tl.fromTo('#b2s',
  { opacity: 0, y: 30, filter: 'blur(20px)' },
  { opacity: 1, y: 0, filter: 'blur(0px)',
    duration: 0.65, ease: 'power3.out' }, 4.45);

tl.fromTo('#b2a',
  { opacity: 0, y: 40, filter: 'blur(12px)' },
  { opacity: 1, y: 0, filter: 'blur(0px)',
    duration: 0.50, ease: 'power3.out' }, 5.20);

tl.fromTo('#b2b',
  { opacity: 0, scale: 0.5, filter: 'blur(20px)',
    textShadow: '0 0 0 rgba(57,255,20,0)' },
  { opacity: 1, scale: 1.0, filter: 'blur(0px)',
    textShadow: '0 0 100px rgba(57,255,20,1), 0 0 200px rgba(57,255,20,0.65)',
    duration: 0.55, ease: 'back.out(2.4)' }, 5.65);

tl.fromTo('#b2c',
  { opacity: 0, y: 24, letterSpacing: '0.20em' },
  { opacity: 1, y: 0, letterSpacing: '0.10em',
    duration: 0.55, ease: 'power2.out' }, 7.20);

// pulse on the punch
tl.to('#b2b', {
  textShadow: '0 0 140px rgba(57,255,20,1), 0 0 280px rgba(57,255,20,0.9)',
  scale: 1.04, duration: 0.40, ease: 'sine.inOut',
  yoyo: true, repeat: 3
}, 6.40);

/* ---------- BEAT 3 — SECRET #2 (10–16s) — ONE HOOK, 50 VARIATIONS ----------
 * 2x2 post tile fades in then text overlays.
 * VO @ 10.30.
 */
tl.fromTo(['#b3p1', '#b3p2', '#b3p3', '#b3p4'],
  { opacity: 0, scale: 0.85, filter: 'blur(15px)' },
  { opacity: 1, scale: 1.0, filter: 'blur(0px)',
    duration: 0.55, ease: 'power3.out',
    stagger: 0.10 }, 10.05);

tl.fromTo('#b3n',
  { opacity: 0, scale: 0.4, filter: 'blur(15px)' },
  { opacity: 1, scale: 1.0, filter: 'blur(0px)',
    textShadow: '0 0 110px rgba(57,255,20,1), 0 0 220px rgba(57,255,20,0.7)',
    duration: 0.55, ease: 'back.out(2.6)' }, 10.45);

tl.fromTo('#b3a',
  { opacity: 0, y: 35, filter: 'blur(12px)' },
  { opacity: 1, y: 0, filter: 'blur(0px)',
    duration: 0.50, ease: 'power3.out' }, 11.40);

tl.fromTo('#b3b',
  { opacity: 0, scale: 0.5, filter: 'blur(20px)',
    textShadow: '0 0 0 rgba(57,255,20,0)' },
  { opacity: 1, scale: 1.0, filter: 'blur(0px)',
    textShadow: '0 0 100px rgba(57,255,20,1), 0 0 200px rgba(57,255,20,0.65)',
    duration: 0.55, ease: 'back.out(2.4)' }, 11.85);

// pulse the tile briefly to suggest "infinite variations"
tl.to(['#b3p1', '#b3p2', '#b3p3', '#b3p4'], {
  scale: 1.03, duration: 0.45, ease: 'sine.inOut',
  yoyo: true, repeat: 2, stagger: 0.05
}, 13.20);

tl.to('#b3b', {
  textShadow: '0 0 140px rgba(57,255,20,1), 0 0 280px rgba(57,255,20,0.9)',
  scale: 1.04, duration: 0.40, ease: 'sine.inOut',
  yoyo: true, repeat: 3
}, 12.80);

/* ---------- BEAT 4 — SECRET #3 (16–22s) — DM-BAIT ----------
 * Comment mock → arrow → DM mock stacks vertically.
 * VO @ 16.30.
 */
tl.fromTo('#b4cm',
  { opacity: 0, x: -120, filter: 'blur(15px)' },
  { opacity: 1, x: 0, filter: 'blur(0px)',
    duration: 0.55, ease: 'power3.out' }, 16.05);

tl.fromTo('#b4ar',
  { opacity: 0, y: -20, scale: 0.6 },
  { opacity: 1, y: 0, scale: 1.0,
    color: '#39FF14', textShadow: '0 0 30px rgba(57,255,20,0.85)',
    duration: 0.40, ease: 'back.out(2.6)' }, 17.30);

tl.fromTo('#b4dm',
  { opacity: 0, x: 120, filter: 'blur(15px)' },
  { opacity: 1, x: 0, filter: 'blur(0px)',
    duration: 0.55, ease: 'power3.out' }, 17.80);

tl.fromTo('#b4n',
  { opacity: 0, scale: 0.4, filter: 'blur(15px)' },
  { opacity: 1, scale: 1.0, filter: 'blur(0px)',
    textShadow: '0 0 110px rgba(57,255,20,1), 0 0 220px rgba(57,255,20,0.7)',
    duration: 0.55, ease: 'back.out(2.6)' }, 19.30);

tl.fromTo('#b4b',
  { opacity: 0, scale: 0.5, filter: 'blur(20px)',
    textShadow: '0 0 0 rgba(57,255,20,0)' },
  { opacity: 1, scale: 1.0, filter: 'blur(0px)',
    textShadow: '0 0 100px rgba(57,255,20,1), 0 0 200px rgba(57,255,20,0.65)',
    duration: 0.55, ease: 'back.out(2.4)' }, 19.75);

tl.fromTo('#b4c',
  { opacity: 0, y: 24, letterSpacing: '0.20em' },
  { opacity: 1, y: 0, letterSpacing: '0.10em',
    duration: 0.55, ease: 'power2.out' }, 20.50);

tl.to('#b4b', {
  textShadow: '0 0 140px rgba(57,255,20,1), 0 0 280px rgba(57,255,20,0.9)',
  scale: 1.04, duration: 0.40, ease: 'sine.inOut',
  yoyo: true, repeat: 2
}, 20.80);

/* ---------- BEAT 5 — SECRET #4 (22–27s) — IT'S ALL AI ----------
 * 3-row stack: CLIPS → SORA / VOICE → ELEVENLABS / CAPTIONS → CHATGPT
 * Then "IT'S ALL AI." stamp pulses.
 * VO @ 22.30.
 */
tl.fromTo('#b5n',
  { opacity: 0, scale: 0.4, filter: 'blur(15px)' },
  { opacity: 1, scale: 1.0, filter: 'blur(0px)',
    textShadow: '0 0 110px rgba(57,255,20,1), 0 0 220px rgba(57,255,20,0.7)',
    duration: 0.55, ease: 'back.out(2.6)' }, 22.05);

tl.fromTo('#b5r1',
  { opacity: 0, x: -150, filter: 'blur(12px)' },
  { opacity: 1, x: 0, filter: 'blur(0px)',
    duration: 0.45, ease: 'power3.out' }, 22.70);

tl.fromTo('#b5r2',
  { opacity: 0, x: -150, filter: 'blur(12px)' },
  { opacity: 1, x: 0, filter: 'blur(0px)',
    duration: 0.45, ease: 'power3.out' }, 23.30);

tl.fromTo('#b5r3',
  { opacity: 0, x: -150, filter: 'blur(12px)' },
  { opacity: 1, x: 0, filter: 'blur(0px)',
    duration: 0.45, ease: 'power3.out' }, 23.90);

tl.fromTo('#b5stamp',
  { opacity: 0, scale: 0.5, rotation: -4, filter: 'blur(20px)' },
  { opacity: 1, scale: 1.0, rotation: 0, filter: 'blur(0px)',
    textShadow: '0 0 100px rgba(57,255,20,1), 0 0 200px rgba(57,255,20,0.7)',
    duration: 0.55, ease: 'back.out(2.6)' }, 25.20);

tl.to('#b5stamp', {
  textShadow: '0 0 150px rgba(57,255,20,1), 0 0 300px rgba(57,255,20,0.95)',
  scale: 1.06, duration: 0.30, ease: 'sine.inOut',
  yoyo: true, repeat: 3
}, 25.80);

/* ---------- BEAT 6 — CTA (27–30s) ----------
 * VO @ 27.30 — "Follow for the playbook."
 */
tl.fromTo('#b6a',
  { opacity: 0, y: 60, filter: 'blur(20px)', letterSpacing: '0.20em' },
  { opacity: 1, y: 0, filter: 'blur(0px)', letterSpacing: '0.02em',
    duration: 0.50, ease: 'power3.out' }, 27.05);

tl.fromTo('#b6b',
  { opacity: 0, scale: 0.5, filter: 'blur(15px)',
    textShadow: '0 0 0 rgba(57,255,20,0)' },
  { opacity: 1, scale: 1.0, filter: 'blur(0px)',
    textShadow: '0 0 100px rgba(57,255,20,1), 0 0 200px rgba(57,255,20,0.65)',
    duration: 0.55, ease: 'back.out(2.4)' }, 27.55);

tl.fromTo('#b6c',
  { opacity: 0, y: 28, letterSpacing: '0.20em' },
  { opacity: 1, y: 0, letterSpacing: '0.08em',
    duration: 0.45, ease: 'power2.out' }, 28.15);

tl.to('#b6b', {
  textShadow: '0 0 140px rgba(57,255,20,1), 0 0 280px rgba(57,255,20,0.95)',
  scale: 1.05, duration: 0.30, ease: 'power2.inOut',
  yoyo: true, repeat: 3
}, 28.45);

window.__timelines['main'] = tl;
