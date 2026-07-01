#!/usr/bin/env node
/**
 * swap_active_reel.js — flip which reel sits at reels/index.html.
 *
 * The HyperFrames CLI always renders reels/index.html. To work on more
 * than one reel concurrently, we keep:
 *   - reels/index.html        — the currently-active composition
 *   - reels/index_reelN.html  — drafts for other reels (NO data-composition-id)
 *
 * "Activating" a draft means:
 *   1. Move the current index.html aside under its draft name
 *   2. Move the chosen draft to index.html
 *   3. Add data-composition-id="main" to its #root
 *   4. Strip data-composition-id from the deactivated file
 *
 * The lint rule "multiple_root_compositions" is what forces this dance —
 * only one root file in reels/ may carry the composition-id attribute at
 * a time.
 *
 * Usage:
 *   node scripts/swap_active_reel.js status
 *   node scripts/swap_active_reel.js activate reel2
 *   node scripts/swap_active_reel.js activate reel3
 */

const fs = require('fs');
const path = require('path');

const REELS_DIR = path.resolve(__dirname, '..', 'reels');
const ROOT_OPEN_RE = /<div\s+id="root"([^>]*)>/;
const ATTR_RE = /\s*data-composition-id="[^"]*"/;

function fail(msg) { console.error(`error: ${msg}`); process.exit(1); }

function readActiveReel() {
  const idx = path.join(REELS_DIR, 'index.html');
  if (!fs.existsSync(idx)) return null;
  const html = fs.readFileSync(idx, 'utf8');
  const m = html.match(/<audio[^>]+src="assets\/audio\/([^"]+)"/);
  return m ? m[1] : '(unknown)';
}

function listDrafts() {
  return fs.readdirSync(REELS_DIR)
    .filter(f => /^index_reel\d+\.html$/.test(f))
    .map(f => f.replace(/^index_(reel\d+)\.html$/, '$1'));
}

function setCompositionId(filePath, on) {
  const html = fs.readFileSync(filePath, 'utf8');
  const match = html.match(ROOT_OPEN_RE);
  if (!match) fail(`#root <div> not found in ${filePath}`);
  let attrs = match[1].replace(ATTR_RE, '');
  if (on) attrs = ' data-composition-id="main"' + attrs;
  const next = html.replace(ROOT_OPEN_RE, `<div id="root"${attrs}>`);
  fs.writeFileSync(filePath, next);
}

function activate(target) {
  if (!/^reel\d+$/.test(target)) fail(`expected reelN (e.g. reel2), got: ${target}`);
  const draft = path.join(REELS_DIR, `index_${target}.html`);
  const active = path.join(REELS_DIR, 'index.html');
  if (!fs.existsSync(draft)) fail(`draft not found: ${draft}`);

  // Figure out which reel is currently active by inspecting its audio src
  const currentAudio = readActiveReel();
  let currentName = 'reel?';
  if (currentAudio && /^reel(\d+)_/.test(currentAudio)) {
    currentName = 'reel' + currentAudio.match(/^reel(\d+)_/)[1];
  } else if (currentAudio && /genz-ai-stack/.test(currentAudio)) {
    currentName = 'reel3';
  }
  if (currentName === target) {
    console.log(`${target} is already active.`);
    return;
  }

  const archivedDraft = path.join(REELS_DIR, `index_${currentName}.html`);
  if (fs.existsSync(archivedDraft)) {
    fail(`refusing to overwrite existing draft: ${archivedDraft}`);
  }

  // 1. Demote current active → its draft slot
  setCompositionId(active, false);
  fs.renameSync(active, archivedDraft);

  // 2. Promote chosen draft → index.html
  fs.renameSync(draft, active);
  setCompositionId(active, true);

  console.log(`activated ${target} (was ${currentName})`);
  console.log(`  reels/index.html       ← was reels/index_${target}.html`);
  console.log(`  reels/index_${currentName}.html  ← was reels/index.html`);
}

function status() {
  const active = readActiveReel();
  console.log(`active audio: ${active || '(no index.html)'}`);
  const drafts = listDrafts();
  console.log(`drafts:        ${drafts.length ? drafts.join(', ') : '(none)'}`);
}

const [, , cmd, target] = process.argv;
if (cmd === 'status' || !cmd) {
  status();
} else if (cmd === 'activate') {
  if (!target) fail('usage: node scripts/swap_active_reel.js activate <reelN>');
  activate(target);
} else {
  fail(`unknown command: ${cmd}`);
}
