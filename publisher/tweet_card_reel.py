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
  6. Composite the final mp4 (publisher/compositor.py).
  7. Upload to Drive (reuse publisher/publish_reel.upload_to_drive).
  8. Write Reel MP4 URL + Status="Ready to Post" back to the row.

If anything fails: row Status is set to "Render Failed" with the
error truncated into the Media Status cell for debugging.
"""

from __future__ import annotations

import argparse
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
        else:  # dry run: graft winners onto the in-memory row only
            v = (result["video"]["winner"] or {}).get("media_url", "")
            i = (result["image"]["winner"] or {}).get("media_url", "")
            if v:
                row["Media Video URL"] = v
            if i:
                row["Media Image URL"] = i
        log.info("Media found -> video=%s image=%s",
                 bool(row.get("Media Video URL", "").strip()),
                 bool(row.get("Media Image URL", "").strip()))
    except Exception as exc:  # noqa: BLE001 — finder is best-effort
        log.warning("Auto media-find failed on row %d: %s", row_index, exc)
    return row


# ---------------------------------------------------------------------------

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

    # Download the source video. If it can't be fetched (download blocked,
    # empty result, error), the post is SKIPPED — no still fallback.
    log.info("Downloading source video: %s", video_url)
    try:
        source_video = fetch_single_clip(video_url, slug, max_seconds=60.0)
    except Exception as exc:  # noqa: BLE001 — map to skip
        raise NoVideoError(
            f"Row {row_index}: source video download failed ({exc})"
        ) from exc
    if not source_video or not Path(source_video).exists():
        raise NoVideoError(
            f"Row {row_index}: source video produced no file ({video_url})"
        )

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

    out_mp4 = RENDERS_DIR / f"{slug}-tweet.mp4"
    log.info("Compositing reel (video) -> %s", out_mp4.name)
    composite_reel(
        card_png, source_video, poster_path, out_mp4,
        preview_seconds=1.0,
        max_seconds=60.0,
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

    # Self-serve media: if the row has a Topic but no Media Video/Image URL,
    # find it here (keyless: yt-dlp + Pexels + brand scrape). This means a row
    # only needs Topic + "Ready to Run" — no dependency on n8n's YouTube-API
    # search, which is the part that keeps breaking ($env block, Merge config).
    if not (row.get("Media Video URL", "").strip()
            and row.get("Media Image URL", "").strip()):
        row = _ensure_media(reader, row_index, row, dry_run=dry_run)

    # Hard rule: no real video clip -> SKIP the post (don't ship a still,
    # don't mark it Failed). Routes to the terminal SKIPPED_STATUS below.
    if not row.get("Media Video URL", "").strip():
        msg = (f"Row {row_index}: no video clip found for "
               f"{row.get('Topic', '')!r} — post skipped.")
        log.warning(msg)
        if not dry_run:
            _try_update(reader, row_index, "Status", SKIPPED_STATUS)
            _try_update(reader, row_index, "Media Status", msg[:200])
        return 0

    try:
        mp4_path = build_reel_for_row(row)
    except NoVideoError as exc:
        # No real footage -> SKIP the post (terminal, not a failure/retry).
        log.warning("Skipping row %d — no usable video: %s", row_index, exc)
        if not dry_run:
            _try_update(reader, row_index, "Status", SKIPPED_STATUS)
            _try_update(reader, row_index, "Media Status", str(exc)[:200])
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

    # Stage the reel onto Instagram (upload + process, but DO NOT publish).
    # The user's rule: "Stage only, I tap Publish." Best-effort — if the IG
    # token is expired or anything fails, the render + Drive + email still
    # stand; the email just tells the user to post from Drive instead.
    stage = _stage_on_instagram(reader, row_index, download_url, row)

    # Email the user: Drive link + the Post Caption (with hashtags), and
    # whether the reel is staged on IG ready to tap Publish, or needs manual
    # posting. Best-effort — a notify failure must never undo a good render.
    _send_review_email(row, download_url, stage)

    log.info("Done. Row %d -> Status=%s, mp4=%s", row_index, DONE_STATUS, download_url)
    return 0


def _stage_on_instagram(reader, row_index: int, video_url: str, row: dict):
    """Best-effort: stage the reel on IG (no publish) and record the outcome
    in the sheet. Returns the StageResult (ok=False on any failure)."""
    from publisher.stage_instagram import stage_reel  # late import

    caption = (row.get("Post Caption") or "").strip()
    result = stage_reel(video_url, caption)
    if result.ok:
        _try_update(reader, row_index, "Instagram Post ID", result.container_id)
        _try_update(reader, row_index, "Instagram Post",
                    "Ready - 1-click publish from review email")
        log.info("Row %d staged on IG (container=%s).",
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
