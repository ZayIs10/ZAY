"""Build one @execute-style tweet-card reel for a Reels-tab row.

This is the entry script the GitHub Actions render worker invokes
(see .github/workflows/build_tweet_card_reel.yml). It can also be
run locally for dry-runs.

Pipeline:
  1. Read the row from the Reels tab.
  2. Validate Post Caption + Media Video URL + Media Image URL.
  3. Download the source video (yt-dlp / HTTP).
  4. Download the poster image.
  5. Render the tweet-card PNG (publisher/tweet_card.py).
  6. Fetch a viral hook opener clip (publisher/hook_opener.py,
     viralhooks.org — best-effort, reel builds without it on failure).
  7. Composite the final mp4 (publisher/compositor.py): whole hook
     full-screen first (clean, no card), then poster intro + clip with
     the tweet card overlaid.
  8. Upload to Drive (reuse publisher/publish_reel.upload_to_drive).
  9. Write Reel MP4 URL + Status="Ready to Post" back to the row.

If anything fails: row Status is set to "Render Failed" with the
error truncated into the Media Status cell for debugging.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from publisher.post_generator import SheetsReader  # noqa: E402
from publisher.media_consumer import fetch_single_clip, fetch_image  # noqa: E402
from publisher.tweet_card import render as render_card  # noqa: E402
from publisher.compositor import build as composite_reel  # noqa: E402

RENDERS_DIR = REPO_ROOT / "renders"
TMP_DIR = REPO_ROOT / ".tmp" / "tweet_card_reel"
LOGO_PATH = REPO_ROOT / "logo.png"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("tweet_card_reel")


# ---------------------------------------------------------------------------

def _slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:40] or "reel"


def _youtube_thumbnail_url(video_url: str) -> str:
    """Derive a poster image from a YouTube watch URL's video id. Used as a
    fallback when the row has no Media Image URL (e.g. niche topics where
    the brand-blog og:image scrape comes up empty). Returns '' for non-YT."""
    m = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", video_url or "")
    if not m:
        return ""
    vid = m.group(1)
    return f"https://img.youtube.com/vi/{vid}/maxresdefault.jpg"


def _candidate_video_urls(row: dict) -> list[str]:
    """Ordered, de-duplicated list of video URLs to try for this row:
    the primary 'Media Video URL' first, then any VIDEO backups the finder
    stored in 'Media Backups (JSON)'. This is what lets a row recover when the
    top pick won't download — we try the next-best on-topic clip instead of
    skipping (user's rule: no Pexels, no silent skip)."""
    urls: list[str] = []
    seen: set[str] = set()

    def _add(u: str) -> None:
        u = (u or "").strip()
        if u and u not in seen:
            seen.add(u)
            urls.append(u)

    _add(row.get("Media Video URL", ""))

    raw = (row.get("Media Backups (JSON)") or "").strip()
    if raw:
        try:
            data = json.loads(raw)
            for c in (data.get("video") or []):
                # backups store both media_url (download target) + page_url;
                # for YouTube they're the same watch URL.
                _add(c.get("media_url") or c.get("page_url") or "")
        except (ValueError, TypeError, AttributeError) as exc:
            log.warning("Could not parse Media Backups (JSON): %s", exc)
    return urls


def _sheets_config() -> dict:
    load_dotenv(REPO_ROOT / ".env")
    sheet_id = os.getenv("GOOGLE_SHEET_ID")
    if not sheet_id:
        raise RuntimeError("GOOGLE_SHEET_ID is not set in .env")
    return {
        "google_sheets": {
            "credentials_file": "google_service_account.json",
            "spreadsheet_id": sheet_id,
            "sheet_name": os.getenv("GOOGLE_SHEET_REELS_NAME", "Reels"),
        }
    }


def _read_row_by_index(reader: SheetsReader, row_index: int) -> dict:
    """Read a single row by 1-indexed sheet row number."""
    all_values = reader.ws.get_all_values()
    if row_index < 2 or row_index > len(all_values):
        raise RuntimeError(f"Row {row_index} out of range")
    headers = all_values[0]
    raw = all_values[row_index - 1]
    row = {headers[j]: raw[j] if j < len(raw) else "" for j in range(len(headers))}
    row["_row_index"] = row_index
    return row


# "Ready to Run" is the explicit GO signal the user sets. It is deliberately
# DIFFERENT from the terminal "Ready to Post" so the build never re-triggers
# itself into an infinite loop. Matched case-insensitively + trimmed because
# the value is hand-typed in the sheet ("ready to run ", "Ready to Run", ...).
TRIGGER_STATUS = "ready to run"
CLAIM_STATUS = "Building"          # set immediately so a re-poll won't double-fire
DONE_STATUS = "Ready to Post"      # terminal: reel is in Drive, do NOT re-build
SKIPPED_STATUS = "Skipped - No Video"  # terminal: no real clip found, post skipped


class NoVideoError(RuntimeError):
    """Raised when no usable source video clip could be found/downloaded.

    The user's rule: a reel MUST use real footage. If there's no clip, we
    SKIP the post rather than ship a still. The runner maps this to the
    terminal SKIPPED_STATUS (not a hard failure / not a retry)."""


def _find_ready_to_run_row(reader: SheetsReader) -> dict | None:
    """Return the first row whose Status is 'Ready to Run' (trimmed,
    case-insensitive) AND has a Topic, or None."""
    all_values = reader.ws.get_all_values()
    if not all_values:
        return None
    headers = all_values[0]
    for i, raw in enumerate(all_values[1:], start=2):
        row = {headers[j]: raw[j] if j < len(raw) else "" for j in range(len(headers))}
        status = str(row.get("Status", "")).strip().lower()
        topic = str(row.get("Topic", "")).strip()
        if status == TRIGGER_STATUS and topic:
            row["_row_index"] = i
            return row
    return None


def _find_row_by_topic(reader: SheetsReader, topic: str) -> dict:
    """Return the row whose Topic matches `topic` (trimmed, case-insensitive).

    This is the ROBUST way for n8n to address a row: it sends the exact Topic
    string (which it already has) instead of a row NUMBER, which silently
    breaks if the sheet is sorted/inserted/deleted between firing and building
    — the bug where 'build Opus 4.8' actually rendered the billionaire row.
    """
    def _norm(s: str) -> str:
        # Collapse any run of whitespace to one space, lowercase, trim. This
        # survives a topic that arrived with a mangled space (e.g. a shell ate
        # a "$1", leaving "Almost  Trillion" with a double space).
        return re.sub(r"\s+", " ", str(s)).strip().lower()

    def _words(s: str) -> list[str]:
        # Alphanumeric word tokens, lowercased. "Almost $1 Trillion" ->
        # ["almost", "1", "trillion"]; "Almost  Trillion" -> ["almost",
        # "trillion"].
        return re.findall(r"[a-z0-9]+", str(s).lower())

    def _is_subseq(small: list[str], big: list[str]) -> bool:
        # True if every token of `small` appears in `big` in order (a dropped
        # token like "$1" just means small is missing one of big's words).
        it = iter(big)
        return all(tok in it for tok in small)

    want = _norm(topic)
    all_values = reader.ws.get_all_values()
    if not all_values:
        raise RuntimeError("Sheet is empty")
    headers = all_values[0]

    rows = []
    for i, raw in enumerate(all_values[1:], start=2):
        row = {headers[j]: raw[j] if j < len(raw) else "" for j in range(len(headers))}
        row["_row_index"] = i
        rows.append(row)

    # 1) Exact (whitespace-normalized) match — the normal path.
    matches = [r for r in rows if _norm(r.get("Topic", "")) == want]

    # 2) Forgiving fallback: a topic with a symbol/number lost in transit
    #    ("Almost $1 Trillion" -> "Almost  Trillion"). Accept a sheet row whose
    #    Topic words are a SUPERSET of the received words in order (i.e. only a
    #    token was dropped). ONLY accept when it resolves to exactly one row —
    #    never guess between several. This won't merge "Opus 4.8" vs "Opus
    #    4.5" because their differing digit tokens break the subsequence.
    if not matches:
        want_words = _words(topic)
        if want_words:
            loose_hits = [
                r for r in rows
                if _is_subseq(want_words, _words(r.get("Topic", "")))
            ]
            if len(loose_hits) == 1:
                log.warning(
                    "Topic %r had no exact match; matched row %d via "
                    "word-subsequence fallback (likely a $/number lost in "
                    "transit). Sheet Topic: %r",
                    topic, loose_hits[0]["_row_index"],
                    loose_hits[0].get("Topic"),
                )
                matches = loose_hits

    if not matches:
        raise RuntimeError(f"No row found with Topic == {topic!r}")
    if len(matches) > 1:
        idxs = ", ".join(str(m["_row_index"]) for m in matches)
        raise RuntimeError(
            f"Topic {topic!r} matches {len(matches)} rows ({idxs}); "
            "Topic must be unique to build by topic."
        )
    return matches[0]


def _try_update(reader: SheetsReader, row_index: int, header: str, value: str) -> None:
    """Best-effort cell update — log + skip if the column doesn't exist."""
    try:
        col = reader._col_index(header)
        reader.ws.update_cell(row_index, col, value)
    except (ValueError, IndexError) as exc:
        log.warning("Could not update column %r: %s", header, exc)


def _mark_failed(reader: SheetsReader, row_index: int, msg: str) -> None:
    _try_update(reader, row_index, "Status", "Render Failed")
    _try_update(reader, row_index, "Media Status", msg[:200])


def _prefer_sheet_youtube_url(reader: SheetsReader, row_index: int, row: dict,
                              *, dry_run: bool) -> dict:
    """If the row has a hand-picked 'YouTube URL', make it the clip to use.

    The user curates a specific YouTube link when creating the topic. That
    link must always win over auto-search. We copy it into 'Media Video URL'
    (what the whole build consumes) so the user's pick is never overridden,
    and — if 'Media Image URL' is blank — seed the poster from the video's
    own thumbnail so the build has an image without an extra search.

    No-op when 'YouTube URL' is empty or isn't a recognizable YouTube link
    (so a stray note in that cell can't break the build).
    """
    yt = (row.get("YouTube URL") or "").strip()
    if not yt:
        return row
    # Accept a full watch/share URL or a bare 11-char video id; reject prose.
    m = re.search(r"(?:v=|youtu\.be/|/shorts/)([A-Za-z0-9_-]{11})", yt)
    if not m and re.fullmatch(r"[A-Za-z0-9_-]{11}", yt):
        yt = f"https://www.youtube.com/watch?v={yt}"
        m = re.search(r"v=([A-Za-z0-9_-]{11})", yt)
    if not m:
        log.warning("Row %d 'YouTube URL' isn't a YouTube link (%.40r) — "
                    "ignoring it, will auto-find instead.", row_index, yt)
        return row

    current = (row.get("Media Video URL") or "").strip()
    if current == yt:
        log.info("Row %d already using the sheet's YouTube URL.", row_index)
        return row

    log.info("Row %d: using hand-picked YouTube URL %s (overrides auto-search).",
             row_index, yt)
    row["Media Video URL"] = yt
    # No Media Image URL written — the build derives the poster from this
    # video's own thumbnail at render time (see build_reel_for_row), so a
    # separate still is unnecessary. We only record the hand-picked VIDEO.
    if not dry_run:
        _try_update(reader, row_index, "Media Video URL", yt)
        _try_update(reader, row_index, "Media Source", "sheet:YouTube URL")
        _try_update(reader, row_index, "Media Status", "found")
    return row


def _ensure_media(reader: SheetsReader, row_index: int, row: dict,
                  *, dry_run: bool) -> dict:
    """If the row is missing a video/image URL, discover them (keyless) and
    write them back, then return the refreshed row. No-op on failure — the
    caller still validates and will mark the row failed if media is absent."""
    topic = (row.get("Topic") or "").strip()
    if not topic:
        return row
    log.info("Media missing on row %d — running keyless finder...", row_index)
    try:
        from publisher import media_finder  # noqa: E402
        result = media_finder.discover_for_topic(
            topic, row.get("Key Points") or "",
        )
        if not dry_run:
            media_finder.write_row_media(reader.ws, row_index, result)
            row = _read_row_by_index(reader, row_index)
        else:  # dry run: graft the video winner onto the in-memory row only
            v = (result["video"]["winner"] or {}).get("media_url", "")
            if v:
                row["Media Video URL"] = v
        log.info("Media found -> video=%s (poster derived from video thumb)",
                 bool(row.get("Media Video URL", "").strip()))
    except Exception as exc:  # noqa: BLE001 — finder is best-effort
        log.warning("Auto media-find failed on row %d: %s", row_index, exc)
    return row


# ---------------------------------------------------------------------------

def _ensure_captions(reader: SheetsReader, row_index: int, row: dict,
                     *, dry_run: bool) -> dict:
    """If the row has no Reel Caption AND no Post Caption, generate BOTH here
    (free, no OpenAI) from Topic + Key Points, grounded in the source video's
    transcript when a YouTube/Media video URL is present, then write them back.

    This is what lets a topic be seeded with just Topic + Status='Ready to Run':
    the build self-fills the captions the same way it self-finds the media.
    No-op when either caption is already present (never overwrites the user's
    hand-written copy)."""
    reel_cap = (row.get("Reel Caption") or "").strip()
    post_cap = (row.get("Post Caption") or "").strip()
    if reel_cap and post_cap:
        return row  # both already there — respect hand-written copy

    topic = (row.get("Topic") or "").strip()
    if not topic:
        return row  # nothing to build from; validation will catch it

    key_points = (row.get("Key Points") or "").strip()

    # Ground in real spoken content when the row points at a video. Prefer the
    # hand-picked YouTube URL; fall back to whatever media the finder chose.
    transcript = ""
    video_url = (row.get("YouTube URL") or row.get("Media Video URL") or "").strip()
    if video_url:
        try:
            from publisher.media_sources.transcript_picker import fetch_transcript
            transcript = fetch_transcript(video_url, max_chars=4000)
        except Exception as exc:  # noqa: BLE001 — transcript is best-effort
            log.warning("Transcript fetch failed for row %d (%s); "
                        "captions will use Topic + Key Points only.",
                        row_index, exc)

    log.info("Captions missing on row %d — generating (free)%s...",
             row_index, " with transcript" if transcript else "")

    from publisher.caption_builder import build_captions
    caps = build_captions(topic, key_points, transcript)

    # Only fill the columns that were actually blank.
    if not reel_cap:
        row["Reel Caption"] = caps["reel_caption"]
        if not dry_run:
            _try_update(reader, row_index, "Reel Caption", caps["reel_caption"])
    if not post_cap:
        row["Post Caption"] = caps["post_caption"]
        if not dry_run:
            _try_update(reader, row_index, "Post Caption", caps["post_caption"])

    return row


def build_reel_for_row(row: dict) -> Path:
    """Pure build step — no Sheet I/O. Returns the local mp4 path.

    Kept separate from the sheet-update wrapper so it's straightforward
    to invoke from a notebook / test against a hand-built row dict.
    """
    row_index = row["_row_index"]
    topic = (row.get("Topic") or "").strip()
    # On-screen card text = Reel Caption (col 29): the words that sit ON the
    # reel video, no hashtags, explaining the update + how to use it. The Post
    # Caption (col 30) is the IG text-box caption with hashtags and must NOT be
    # burned onto the video. Fall back to Post Caption only if Reel Caption is
    # empty so legacy rows still render.
    card_text = (row.get("Reel Caption") or "").strip()
    if not card_text:
        card_text = (row.get("Post Caption") or "").strip()
    video_url = (row.get("Media Video URL") or "").strip()
    image_url = (row.get("Media Image URL") or "").strip()

    # A reel MUST use real footage. Topic + Caption + a usable source VIDEO
    # are required. The poster image is OPTIONAL: if the finder didn't get
    # one (e.g. niche topics where the brand-blog scrape finds nothing), we
    # derive a poster from the video's own thumbnail. If no clip can be
    # found/downloaded, the post is SKIPPED (NoVideoError) — never a still.
    missing = []
    if not topic:
        missing.append("Topic")
    if not card_text:
        # Either column satisfies this — card_text already fell back to
        # Post Caption above, so an empty here means BOTH are blank.
        missing.append("Reel Caption / Post Caption")
    if not video_url:
        missing.append("Media Video URL")
    if missing:
        raise RuntimeError(
            f"Row {row_index} missing required fields: {', '.join(missing)}"
        )

    slug = _slugify(topic)
    log.info("Row %d -> slug=%s", row_index, slug)

    TMP_DIR.mkdir(parents=True, exist_ok=True)
    RENDERS_DIR.mkdir(parents=True, exist_ok=True)

    # Download the source video. We try the primary Media Video URL first, then
    # any on-topic VIDEO backups the finder stored — so a single un-downloadable
    # clip no longer skips the whole post (user's rule: no Pexels, no silent
    # skip; try another YouTube clip, alert only if ALL fail). The multi-client
    # cookieless yt-dlp logic in media_consumer handles the bot-block per URL.
    candidates = _candidate_video_urls(row)
    source_video = None
    used_url = ""
    errors: list[str] = []
    for i, cand in enumerate(candidates, start=1):
        label = "primary" if i == 1 else f"backup {i - 1}"
        log.info("Downloading source video (%s of %d, %s): %s",
                 i, len(candidates), label, cand)
        try:
            got = fetch_single_clip(cand, slug, max_seconds=60.0)
        except Exception as exc:  # noqa: BLE001 — try the next candidate
            log.warning("  %s failed: %s", label, exc)
            errors.append(f"{label}: {exc}")
            continue
        if got and Path(got).exists() and Path(got).stat().st_size > 0:
            source_video = got
            used_url = cand
            if i > 1:
                log.info("Recovered using %s (%s) after primary failed.",
                         label, cand)
            break
        log.warning("  %s produced no file.", label)
        errors.append(f"{label}: produced no file")

    if not source_video:
        # Every candidate failed. SKIP the post (terminal) AND alert the user —
        # this is the rare "this row needs a hand-picked link" case.
        joined = "; ".join(errors) or "no video candidates on the row"
        raise NoVideoError(
            f"Row {row_index}: ALL {len(candidates)} video candidate(s) failed "
            f"to download — {joined}"
        )

    # If a backup won, use its URL for the poster-thumbnail derivation below so
    # the poster matches the clip that actually rendered (the primary may be
    # permanently dead). The sheet's Media Video URL is left as-is; the build
    # succeeding is what matters, and the finder will refresh it next run.
    if used_url and used_url != video_url:
        video_url = used_url

    # Poster: use the row's image if present, else fall back to the video's
    # own thumbnail (always available for a YouTube clip) so a missing image
    # never fails the build.
    if not image_url:
        image_url = _youtube_thumbnail_url(video_url)
        if image_url:
            log.info("No poster image — using video thumbnail: %s", image_url)

    poster_path = REPO_ROOT / "assets" / "images" / "auto" / f"{slug}_auto.jpg"
    if image_url:
        log.info("Downloading poster image: %s", image_url)
        try:
            poster_rels = fetch_image(image_url, slug, count=1)
        except Exception as exc:  # noqa: BLE001
            log.warning("Poster image fetch failed (%s)", exc)
            poster_rels = []
        if not poster_path.exists() and poster_rels:
            # fetch_image returns paths relative to reels/index.html; resolve.
            poster_path = (REPO_ROOT / "reels" / poster_rels[0]).resolve()
    if not poster_path.exists():
        raise RuntimeError(f"Poster image download failed for row {row_index}")

    card_png = TMP_DIR / f"{slug}_card.png"
    log.info("Rendering tweet card -> %s", card_png.name)
    render_card(
        card_text,
        handle="@genzcapital",
        display_name="Gen Z Capital",
        avatar_path=LOGO_PATH,
        out_path=card_png,
    )

    # Viral hook opener (viralhooks.org): the whole hook clip plays first,
    # full-screen and CLEAN (the card appears only when the body starts —
    # user's call 2026-07-19), then the normal body. Best-effort by
    # contract — fetch_hook_for_topic returns None on ANY failure and the
    # reel builds exactly as before. Deterministic per Topic (re-runs pick
    # the same hook). Kill-switch: DISABLE_VIRAL_HOOK=1.
    hook_path = None
    try:
        from publisher.hook_opener import fetch_hook_for_topic  # noqa: E402
        hook_path = fetch_hook_for_topic(topic, TMP_DIR)
    except Exception as exc:  # noqa: BLE001 — hook must never kill a build
        log.warning("viral hook: unavailable (%s) — building without an "
                    "opener.", exc)
    if hook_path:
        log.info("Viral hook opener: %s", Path(hook_path).name)
    else:
        log.info("No viral hook opener this build (disabled or "
                 "unavailable) — rendering the plain reel.")

    out_mp4 = RENDERS_DIR / f"{slug}-tweet.mp4"
    log.info("Compositing reel (video) -> %s", out_mp4.name)
    composite_reel(
        card_png, source_video, poster_path, out_mp4,
        preview_seconds=1.0,
        max_seconds=60.0,
        hook_video=hook_path,
    )

    if not out_mp4.exists() or out_mp4.stat().st_size == 0:
        raise RuntimeError(f"Composite produced empty file: {out_mp4}")

    log.info("Reel built: %s (%.1f MB)", out_mp4, out_mp4.stat().st_size / 1e6)
    return out_mp4


def run(row_index: int | None, *, topic: str | None = None, dry_run: bool) -> int:
    config = _sheets_config()
    reader = SheetsReader(config)

    if topic is not None:
        # Preferred path: address the row by its unambiguous Topic string.
        row = _find_row_by_topic(reader, topic)
        row_index = row["_row_index"]
    elif row_index is None:
        row = _find_ready_to_run_row(reader)
        if row is None:
            log.info("No row with Status='Ready to Run' (+Topic). Nothing to do.")
            return 0
        row_index = row["_row_index"]
    else:
        row = _read_row_by_index(reader, row_index)

    log.info("Processing row %d: %r", row_index, row.get("Topic", "(no topic)"))

    # Claim the row IMMEDIATELY so a re-poll (n8n fires every minute) sees
    # "Building", not "Ready to Run", and won't kick off a duplicate render.
    if not dry_run:
        _try_update(reader, row_index, "Status", CLAIM_STATUS)

    # USER'S HAND-PICKED CLIP WINS. If the row's "YouTube URL" column is filled
    # (the link chosen when the topic was created), use THAT exact video and
    # skip auto-search entirely — never override the user's pick. It's copied
    # into "Media Video URL" so every downstream step (skip-check, staging,
    # the sheet itself) reflects what's actually used. Auto-search below only
    # runs for rows where YouTube URL was left blank.
    row = _prefer_sheet_youtube_url(reader, row_index, row, dry_run=dry_run)

    # Self-serve media: if the row has a Topic but no Media Video URL, find a
    # clip here (keyless: yt-dlp + Pexels + brand scrape). This means a row only
    # needs Topic + "Ready to Run" — no dependency on n8n's YouTube-API search,
    # which is the part that keeps breaking ($env block, Merge config).
    # Only the VIDEO matters: the poster is derived from the video thumbnail at
    # render time, so a missing Media Image URL never triggers a re-search.
    if not row.get("Media Video URL", "").strip():
        row = _ensure_media(reader, row_index, row, dry_run=dry_run)

    # Self-serve captions: if the row was seeded bare (Topic + "Ready to Run"
    # only), generate the Reel Caption + Post Caption here (free, no OpenAI),
    # grounded in the source video's transcript when there is one. Runs AFTER
    # media so even an auto-found clip can ground the copy. This is why the
    # "missing required fields: Reel Caption / Post Caption" error no longer
    # stops a bare row from building.
    row = _ensure_captions(reader, row_index, row, dry_run=dry_run)

    # Hard rule: no real video clip -> SKIP the post (don't ship a still,
    # don't mark it Failed). Routes to the terminal SKIPPED_STATUS below.
    if not row.get("Media Video URL", "").strip():
        msg = (f"Row {row_index}: no video clip found for "
               f"{row.get('Topic', '')!r} — post skipped.")
        log.warning(msg)
        if not dry_run:
            _try_update(reader, row_index, "Status", SKIPPED_STATUS)
            _try_update(reader, row_index, "Media Status", msg[:200])
            _alert_no_video(row, row_index, msg)
        return 0

    try:
        mp4_path = build_reel_for_row(row)
    except NoVideoError as exc:
        # No real footage even after trying every candidate -> SKIP (terminal,
        # not a failure/retry) AND alert the user so it's never silent.
        log.warning("Skipping row %d — no usable video: %s", row_index, exc)
        if not dry_run:
            _try_update(reader, row_index, "Status", SKIPPED_STATUS)
            _try_update(reader, row_index, "Media Status", str(exc)[:200])
            _alert_no_video(row, row_index, str(exc))
        return 0
    except Exception as exc:
        log.error("Build failed: %s", exc)
        log.debug(traceback.format_exc())
        _mark_failed(reader, row_index, str(exc))
        return 1

    if dry_run:
        log.info("DRY RUN: skipping Drive upload + Sheet update.")
        log.info("Local file: %s", mp4_path)
        return 0

    # Late import so dry runs don't require googleapiclient / OAuth setup.
    from publisher.publish_reel import upload_to_drive  # noqa: E402

    try:
        download_url = upload_to_drive(mp4_path)
    except Exception as exc:
        log.error("Drive upload failed: %s", exc)
        log.debug(traceback.format_exc())
        _mark_failed(reader, row_index, f"Drive upload: {exc}")
        return 2

    _try_update(reader, row_index, "Reel MP4 URL", download_url)
    _try_update(
        reader, row_index, "Media Found At",
        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    _try_update(reader, row_index, "Status", DONE_STATUS)

    # DO NOT publish to Instagram here. The IG API can't truly schedule (sending
    # scheduled_publish_time is silently ignored and the reel posts instantly —
    # verified against a live post 2026-06-18). Instead the row is left at
    # Status="Ready to Post" and a daily GitHub cron (publish_due_reels.py /
    # publish_due_reels.yml) publishes it at 8pm SGT — the peak SG/MY window.
    # So the build just renders + queues; the cron does the timed publish.

    # Email the user: Drive link + the Post Caption (with hashtags), telling
    # them the reel is queued to auto-publish at 8pm SGT (with a Drive link to
    # post sooner if they want). Best-effort — a notify failure must never undo
    # a good render.
    _send_review_email(row, download_url, None)

    log.info("Done. Row %d -> Status=%s, mp4=%s", row_index, DONE_STATUS, download_url)
    return 0


def _stage_on_instagram(reader, row_index: int, video_url: str, row: dict):
    """Best-effort: SCHEDULE the reel on IG ~24h out so it appears in Meta
    Business Suite Planner (where the user can preview video + caption before
    it goes live), and record the outcome in the sheet. Returns the
    StageResult (ok=False on any failure)."""
    from publisher.stage_instagram import schedule_reel_24h  # late import

    caption = (row.get("Post Caption") or "").strip()
    result = schedule_reel_24h(video_url, caption)
    if result.ok:
        _try_update(reader, row_index, "Instagram Post ID", result.container_id)
        _try_update(reader, row_index, "Instagram Post",
                    "Scheduled - preview in Meta Planner")
        log.info("Row %d scheduled on IG (media_id=%s).",
                 row_index, result.container_id)
    else:
        _try_update(reader, row_index, "Instagram Post",
                    f"Not staged - post from Drive ({result.detail})"[:200])
        log.warning("Row %d IG staging skipped/failed: %s",
                    row_index, result.detail)
    return result


def _send_review_email(row: dict, drive_url: str, stage=None) -> None:
    """Compose + send the 'ready to review' email from the row's own fields.

    Post Caption already contains the hashtags (the sheet keeps them in one
    field), so it's emailed verbatim as a single copy-paste-ready block.
    `stage` (a StageResult or None) tells the email whether the reel is staged
    on Instagram ready to publish, or needs manual posting from Drive.
    """
    from publisher.notify_email import build_review_email, send  # late import

    staged_ok = bool(stage and getattr(stage, "ok", False))
    stage_detail = getattr(stage, "detail", "") if stage else ""
    container_id = getattr(stage, "container_id", "") if stage else ""

    subject, body = build_review_email(
        topic=(row.get("Topic") or "").strip(),
        caption=(row.get("Post Caption") or "").strip(),
        drive_url=drive_url,
        staged_ok=staged_ok,
        stage_detail=stage_detail,
        container_id=container_id,
        repo=_github_repo(),
    )
    send(subject, body)


def _alert_no_video(row: dict, row_index: int, detail: str) -> None:
    """Email the user that a row couldn't get a usable video, so it's NEVER a
    silent skip. They can paste a YouTube link into the row's 'YouTube URL'
    column and re-run. Best-effort: a notify failure must not crash the build.
    (User rule: no Pexels fallback, no silent skip — alert me instead.)"""
    try:
        from publisher.notify_email import send  # late import
        topic = (row.get("Topic") or "").strip() or f"row {row_index}"
        subject = f"[GenZ ALERT] No video for reel — {topic}"
        body = (
            f"The reel build couldn't download a usable video for this topic, "
            f"so the post was skipped (no Pexels fallback, by design).\n\n"
            f"Topic: {topic}\n"
            f"Row:   {row_index}\n\n"
            f"What happened:\n{detail}\n\n"
            f"To fix: open the Reels sheet, paste a working YouTube link into "
            f"this row's 'YouTube URL' column, set Status back to "
            f"'Ready to Run', and it'll rebuild using your exact clip.\n"
        )
        send(subject, body)
        log.info("No-video alert email sent for row %d.", row_index)
    except Exception as exc:  # noqa: BLE001 — alerting must never crash a build
        log.warning("Could not send no-video alert for row %d: %s",
                    row_index, exc)


def _github_repo() -> str:
    """Return the 'owner/repo' slug used to build the 1-click publish links.

    In CI, GitHub sets GITHUB_REPOSITORY. Locally it falls back to the origin
    remote, then to the known repo so the email links are never broken.
    """
    env = (os.getenv("GITHUB_REPOSITORY") or "").strip()
    if env:
        return env
    try:
        import subprocess  # noqa: PLC0415
        url = subprocess.check_output(
            ["git", "remote", "get-url", "origin"],
            text=True, stderr=subprocess.DEVNULL,
        ).strip()
        slug = url.rsplit("github.com", 1)[-1].lstrip(":/")
        if slug.endswith(".git"):
            slug = slug[:-4]
        if "/" in slug:
            return slug
    except Exception:  # noqa: BLE001 — never fail the email over a repo lookup
        pass
    return "ZayIs10/ZAY"


# ---------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--row", type=int, help="1-indexed sheet row to render")
    g.add_argument("--topic", type=str,
                   help="Render the row whose Topic matches this (preferred: "
                        "robust against the sheet being sorted/reordered)")
    g.add_argument("--next", action="store_true",
                   help="Render the next Status='Ready to Run' row")
    p.add_argument("--dry-run", action="store_true",
                   help="Build mp4 locally, skip Drive upload + Sheet update")
    args = p.parse_args(argv)

    return run(
        args.row if not (args.next or args.topic) else None,
        topic=args.topic,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
