"""carousel_review.py — the REVIEW GATE between a rendered carousel and Instagram.

The user's rule: the image automation is not mature enough to go
reference -> paid image -> Instagram unsupervised. So after the cloud build
renders a deck, this module:

  1. uploads the WHOLE deck folder (slides + caption.txt) to Google Drive
     ("GenZ Capital Carousels/<deck-name>"), link-viewable, ONE folder link;
  2. emails the user that link + the copy-paste-ready caption for review
     (Gmail SMTP — same notify_email transport the reels use);
  3. updates the Google Sheet row (matched by TOPIC, never row number):
     Status -> "Ready to Post" + best-effort writes the Drive folder URL.

Approval = the user flips the row to "Approved to Post"; a later publish step
(blocked on the instagram_content_publish permission) will watch for that.

Everything here is BEST-EFFORT by design: a failed email or Sheet write must
never fail an otherwise-successful render (the reels' hard-learned rule).

Drive auth is the user's OAuth token (google_drive_token.json) because the
service account has no storage quota; Sheets auth is the service account.
"""

from __future__ import annotations

import logging
import mimetypes
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from publisher.publish_reel import _drive_oauth_creds  # noqa: E402
from publisher import notify_email  # noqa: E402

log = logging.getLogger("carousel_review")

CAROUSEL_DRIVE_FOLDER = "GenZ Capital Carousels"

# Sheet status machine — IDENTICAL semantics to the reels (memory:
# reel_status_state_machine): the trigger word and the done word are
# deliberately different so a poller can never re-fire a finished row.
TRIGGER_STATUS = "ready to run"     # user sets this (matched trimmed+lower)
CLAIM_STATUS = "Building"           # n8n sets this the moment it dispatches
DONE_STATUS = "Ready to Post"       # we set this: deck is in Drive, review sent
FAILED_STATUS = "Render Failed"
APPROVED_STATUS = "Approved to Post"  # user sets this -> future IG publish step


# ---------------------------------------------------------------------------
# 1) Drive: upload the deck FOLDER, return one shareable folder link
# ---------------------------------------------------------------------------
def _get_or_create_folder(drive, name: str, parent_id: str | None) -> str:
    q = (f"name='{name}' and mimeType='application/vnd.google-apps.folder' "
         "and trashed=false")
    if parent_id:
        q += f" and '{parent_id}' in parents"
    resp = drive.files().list(q=q, spaces="drive",
                              fields="files(id,name)").execute()
    folders = resp.get("files", [])
    if folders:
        return folders[0]["id"]
    body = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent_id:
        body["parents"] = [parent_id]
    folder = drive.files().create(body=body, fields="id").execute()
    log.info("Created Drive folder '%s'", name)
    return folder["id"]


def _upload_or_replace(drive, path: Path, folder_id: str) -> str:
    """Upload a file into the folder; if a file with the same name already
    exists there (a re-render), update it in place instead of duplicating."""
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    media = MediaFileUpload(str(path), mimetype=mime, resumable=False)
    resp = drive.files().list(
        q=(f"name='{path.name}' and '{folder_id}' in parents "
           "and trashed=false"),
        spaces="drive", fields="files(id)").execute()
    existing = resp.get("files", [])
    if existing:
        file_id = existing[0]["id"]
        drive.files().update(fileId=file_id, media_body=media).execute()
        return file_id
    file = drive.files().create(
        body={"name": path.name, "parents": [folder_id]},
        media_body=media, fields="id").execute()
    return file["id"]


def upload_deck_to_drive(deck_dir: Path) -> str:
    """Upload every slide + caption.txt + manifest in `deck_dir` to
    Drive under "GenZ Capital Carousels/<deck-name>", make the folder
    link-viewable, and return the folder URL (ONE link to review it all)."""
    creds = _drive_oauth_creds()
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)

    root_id = _get_or_create_folder(drive, CAROUSEL_DRIVE_FOLDER, None)
    deck_id = _get_or_create_folder(drive, deck_dir.name, root_id)

    files = sorted(p for p in deck_dir.iterdir()
                   if p.suffix.lower() in {".png", ".jpg", ".txt", ".json",
                                           ".mp4"})
    for p in files:
        _upload_or_replace(drive, p, deck_id)
        log.info("  Drive <- %s", p.name)

    # one link-viewable permission on the FOLDER covers every file inside
    drive.permissions().create(
        fileId=deck_id, body={"role": "reader", "type": "anyone"},
        fields="id").execute()

    url = f"https://drive.google.com/drive/folders/{deck_id}"
    log.info("Deck uploaded: %s (%d files)", url, len(files))
    return url


# ---------------------------------------------------------------------------
# 2) Review email (reuses the reels' Gmail SMTP transport)
# ---------------------------------------------------------------------------
def send_review_email(*, topic: str, caption: str, folder_url: str,
                      n_slides: int, cost_line: str = "") -> bool:
    subject = f"[GenZ carousel ready to review] {topic}"
    body = (
        f"Your carousel for \"{topic}\" is rendered and uploaded.\n\n"
        f"REVIEW ALL {n_slides} SLIDES (one folder):\n{folder_url}\n\n"
        + (f"Build cost: {cost_line}\n\n" if cost_line else "")
        + "------------------------------------------------------------\n"
          "INSTAGRAM CAPTION (copy-paste ready — includes hashtags)\n"
          "------------------------------------------------------------\n"
        + (caption.strip() or "(no caption)") + "\n\n"
          "To approve for posting, set the row's Status to "
          f"\"{APPROVED_STATUS}\" in the Sheet.\n"
    )
    return notify_email.send(subject, body)


# ---------------------------------------------------------------------------
# 3) Sheet status — address the row by TOPIC (row numbers drift on re-sort)
# ---------------------------------------------------------------------------
def _carousel_sheet_reader():
    from publisher.post_generator import SheetsReader  # lazy: needs gspread
    load_dotenv(REPO_ROOT / ".env")
    sheet_id = os.getenv("GOOGLE_SHEET_ID")
    if not sheet_id:
        raise RuntimeError("GOOGLE_SHEET_ID is not set in .env")
    return SheetsReader({
        "google_sheets": {
            "credentials_file": "google_service_account.json",
            "spreadsheet_id": sheet_id,
            "sheet_name": os.getenv("GOOGLE_SHEET_CAROUSELS_NAME", "Sheet1"),
        }
    })


def _find_row_by_topic(reader, topic: str) -> dict | None:
    want = topic.strip().lower()
    all_values = reader.ws.get_all_values()
    if not all_values:
        return None
    headers = all_values[0]
    for i, raw in enumerate(all_values[1:], start=2):
        row = {headers[j]: raw[j] if j < len(raw) else ""
               for j in range(len(headers))}
        if str(row.get("Topic", "")).strip().lower() == want:
            row["_row_index"] = i
            return row
    return None


def _try_update(reader, row_index: int, header: str, value: str) -> None:
    """Best-effort cell update — log + skip if the column doesn't exist."""
    try:
        col = reader._col_index(header)
        reader.ws.update_cell(row_index, col, value)
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not update column %r: %s", header, exc)


def update_sheet_status(topic: str, status: str,
                        drive_url: str = "") -> bool:
    """Set the carousel row's Status (matched by Topic) + Drive URL.
    Returns True if the row was found and updated."""
    try:
        reader = _carousel_sheet_reader()
        row = _find_row_by_topic(reader, topic)
        if not row:
            log.warning("No Sheet row with Topic == %r — skipping status.",
                        topic)
            return False
        idx = row["_row_index"]
        _try_update(reader, idx, "Status", status)
        if drive_url:
            _try_update(reader, idx, "Drive URL", drive_url)
        log.info("Sheet row %d (%r): Status -> %s", idx, topic, status)
        return True
    except Exception as exc:  # noqa: BLE001 — sheet failure must not fail render
        log.warning("Sheet status update failed for %r: %s", topic, exc)
        return False


# ---------------------------------------------------------------------------
# The one-call review gate the pipeline uses after a successful render
# ---------------------------------------------------------------------------
def review_gate(*, topic: str, deck_dir: Path, caption: str,
                n_slides: int, cost_line: str = "",
                update_sheet: bool = False) -> dict:
    """Upload deck -> email review -> (optionally) mark the Sheet row.
    Every step is best-effort; returns what happened."""
    out = {"drive_url": "", "emailed": False, "sheet_updated": False}

    try:
        out["drive_url"] = upload_deck_to_drive(deck_dir)
    except Exception as exc:  # noqa: BLE001
        log.error("Drive deck upload failed: %s", exc)

    if out["drive_url"]:
        out["emailed"] = send_review_email(
            topic=topic, caption=caption, folder_url=out["drive_url"],
            n_slides=n_slides, cost_line=cost_line)
    else:
        log.warning("No Drive link — skipping review email.")

    if update_sheet:
        # Only mark Ready to Post when there is actually a deck to review.
        if out["drive_url"]:
            out["sheet_updated"] = update_sheet_status(
                topic, DONE_STATUS, out["drive_url"])
        else:
            out["sheet_updated"] = update_sheet_status(topic, FAILED_STATUS)
    return out
