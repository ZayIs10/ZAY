"""Publish a finished Reel MP4 to Instagram via the Graph API.

Pipeline:
  1. Upload the MP4 to Google Drive via the existing service account.
  2. Make the file publicly readable, grab a direct-download URL.
  3. Create an Instagram media container (media_type=REELS, video_url=...).
  4. Poll the container's status_code until it reaches FINISHED.
  5. Publish the container.
  6. Print the resulting Instagram permalink.

Requires (all already in .env):
    INSTAGRAM_ACCESS_TOKEN   long-lived user token with instagram_basic +
                             instagram_content_publish scopes
    INSTAGRAM_IG_USER_ID     numeric IG business/creator account ID

Requires google_service_account.json at the repo root with Drive scope on
the service account (drive.readonly + drive.file is enough to create + share).

Usage:
    # Default — publishes the most recent renders/reels_*.mp4
    python publisher/publish_reel.py

    # Pick a specific MP4
    python publisher/publish_reel.py --video renders/reels_2026-04-30_23-08-27.mp4

    # Custom caption (otherwise reads from
    # assets/reel_scripts/reel_01_faceless_montage.md or the default below)
    python publisher/publish_reel.py --caption "Six accounts. 30M followers..."

    # Dry-run: upload to Drive and print the URL, but don't post to Instagram
    python publisher/publish_reel.py --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

REPO_ROOT = Path(__file__).resolve().parents[1]
RENDERS_DIR = REPO_ROOT / "renders"

# OAuth (personal Google account) — service accounts have 0 Drive storage, so
# uploading the finished reel has to authenticate as the user instead.
# google_oauth_client.json  -> downloaded once from Google Cloud Console.
# google_drive_token.json   -> auto-created on first run, then reused silently.
OAUTH_CLIENT_FILE = REPO_ROOT / "google_oauth_client.json"
OAUTH_TOKEN_FILE = REPO_ROOT / "google_drive_token.json"
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]
REEL_DRIVE_FOLDER = "GenZ Capital Reels"

GRAPH_BASE = "https://graph.facebook.com/v21.0"

DEFAULT_CAPTION = """Six accounts. 30M+ followers. Zero faces.

These are the biggest faceless creators on Instagram right now —
@wealth, @technology, @evolving.ai, @businessbulls.in, @futuretech, @getintoai.

They all use the same 3-step formula. One niche. One format. Posted daily.

But there are 4 more tricks they don't talk about.

Comment "NEXT" for the four secrets.

Send this to someone trying to grow without showing their face.

#facelessinstagram #facelesscreator #instagramgrowth #instagramtips
#contentcreation #wealthbuilding #financialfreedom #personalfinance
#passiveincome #moneymindset #aimoney #automateincome #genzwealth
#genzfinance #genzcapital #buildwealth #creatoreconomy #digitalmarketing"""


def latest_render() -> Path:
    candidates = sorted(RENDERS_DIR.glob("reels_*.mp4"))
    if not candidates:
        sys.exit(f"No renders/reels_*.mp4 found in {RENDERS_DIR}")
    return candidates[-1]


def _drive_oauth_creds():
    """Return OAuth credentials for the user's personal Google account.

    Service accounts have 0 Drive storage, so the reel upload must run as the
    user. First call opens a browser for a one-time consent; the resulting
    token is cached in google_drive_token.json and silently refreshed after.
    """
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if OAUTH_TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(
            str(OAUTH_TOKEN_FILE), DRIVE_SCOPES)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        OAUTH_TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
        return creds
    if not OAUTH_CLIENT_FILE.exists():
        sys.exit(
            f"Missing {OAUTH_CLIENT_FILE.name}. Create an OAuth client "
            "(Desktop app) in Google Cloud Console, download the JSON, and "
            f"save it to the repo root as {OAUTH_CLIENT_FILE.name}."
        )
    flow = InstalledAppFlow.from_client_secrets_file(
        str(OAUTH_CLIENT_FILE), DRIVE_SCOPES)
    print("Opening a browser for a one-time Google sign-in...")
    creds = flow.run_local_server(port=0)
    OAUTH_TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    print(f"  Saved token -> {OAUTH_TOKEN_FILE.name} (won't ask again).")
    return creds


def _get_or_create_reel_folder(drive) -> str:
    """Return the Drive folder ID for REEL_DRIVE_FOLDER, creating it if needed.

    With the drive.file scope the query only sees folders this app created,
    so after the first run the existing folder is found and reused.
    """
    resp = drive.files().list(
        q=(f"name='{REEL_DRIVE_FOLDER}' "
           "and mimeType='application/vnd.google-apps.folder' "
           "and trashed=false"),
        spaces="drive",
        fields="files(id,name)",
    ).execute()
    folders = resp.get("files", [])
    if folders:
        return folders[0]["id"]
    folder = drive.files().create(
        body={"name": REEL_DRIVE_FOLDER,
              "mimeType": "application/vnd.google-apps.folder"},
        fields="id",
    ).execute()
    print(f"  Created Drive folder '{REEL_DRIVE_FOLDER}'.")
    return folder["id"]


def upload_to_drive(path: Path) -> str:
    """Upload `path` to the user's personal Drive, make it link-viewable, and
    return a direct-download URL.

    Authenticates as the user via OAuth (google_oauth_client.json) — the
    service account can't be used because it has no storage quota.
    """
    creds = _drive_oauth_creds()
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)

    folder_id = _get_or_create_reel_folder(drive)

    print(f"Uploading {path.name} to Google Drive ({path.stat().st_size / 1e6:.1f} MB)...")
    media = MediaFileUpload(str(path), mimetype="video/mp4", resumable=True)
    file = drive.files().create(
        body={"name": path.name, "mimeType": "video/mp4",
              "parents": [folder_id]},
        media_body=media,
        fields="id,name,webContentLink",
    ).execute()
    file_id = file["id"]
    print(f"  Uploaded. file_id={file_id}")

    # Make link-viewable so the reel opens on your phone without sign-in.
    drive.permissions().create(
        fileId=file_id,
        body={"role": "reader", "type": "anyone"},
        fields="id",
    ).execute()
    print("  Set permission: anyone with link can view.")

    # Direct download URL Instagram's video fetcher accepts
    download_url = f"https://drive.google.com/uc?export=download&id={file_id}"
    return download_url


def create_reel_container(ig_user_id: str, access_token: str,
                          video_url: str, caption: str) -> str:
    print("Creating Instagram Reels media container...")
    resp = requests.post(
        f"{GRAPH_BASE}/{ig_user_id}/media",
        params={
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "share_to_feed": "true",
            "access_token": access_token,
        },
        timeout=60,
    )
    if resp.status_code != 200:
        sys.exit(f"Container creation failed: {resp.status_code}\n{resp.text}")
    container_id = resp.json().get("id")
    print(f"  container_id={container_id}")
    return container_id


def schedule_reel(ig_user_id: str, access_token: str,
                  video_url: str, caption: str,
                  scheduled_publish_time: int) -> str:
    """Create container, wait for FINISHED, then schedule it for a future time.

    `scheduled_publish_time` is a Unix timestamp (must be 10 min–75 days ahead).
    Returns the media_id of the scheduled post (visible in Meta Business Suite).
    """
    container_id = create_reel_container(ig_user_id, access_token, video_url, caption)
    wait_for_container(container_id, access_token)
    print(f"Scheduling reel for Unix time {scheduled_publish_time}...")
    resp = requests.post(
        f"{GRAPH_BASE}/{ig_user_id}/media_publish",
        params={
            "creation_id": container_id,
            "scheduled_publish_time": scheduled_publish_time,
            "access_token": access_token,
        },
        timeout=60,
    )
    if resp.status_code != 200:
        sys.exit(f"Schedule failed: {resp.status_code}\n{resp.text}")
    media_id = resp.json().get("id", "")
    print(f"  Scheduled. media_id={media_id}")
    return media_id


def wait_for_container(container_id: str, access_token: str,
                       max_wait_s: int = 600) -> None:
    """Poll container status until FINISHED. Reels need server-side processing."""
    print("Waiting for Instagram to process the video...")
    deadline = time.time() + max_wait_s
    while time.time() < deadline:
        resp = requests.get(
            f"{GRAPH_BASE}/{container_id}",
            params={
                "fields": "status_code,status",
                "access_token": access_token,
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        status_code = data.get("status_code", "")
        status = data.get("status", "")
        print(f"  status={status_code} ({status})")
        if status_code == "FINISHED":
            return
        if status_code == "ERROR":
            sys.exit(f"Container errored: {data}")
        time.sleep(10)
    sys.exit("Timed out waiting for container to finish.")


def check_container_status(container_id: str, access_token: str) -> tuple[str, str]:
    """Return (status_code, detail) for a container WITHOUT waiting/publishing.

    Used by the click-to-publish workflow's "check only" mode and as the
    pre-flight before publishing: a container can sit in IN_PROGRESS, land on
    FINISHED (ready to publish), or fail with ERROR/EXPIRED. We surface the raw
    status plus the human-readable `status` field so the run log explains why.
    """
    resp = requests.get(
        f"{GRAPH_BASE}/{container_id}",
        params={
            "fields": "status_code,status",
            "access_token": access_token,
        },
        timeout=30,
    )
    if resp.status_code != 200:
        return "ERROR", f"status lookup failed: {resp.status_code} {resp.text}"
    data = resp.json()
    return data.get("status_code", ""), data.get("status", "")


def publish_container(ig_user_id: str, access_token: str,
                      container_id: str) -> str:
    print("Publishing the Reel...")
    resp = requests.post(
        f"{GRAPH_BASE}/{ig_user_id}/media_publish",
        params={"creation_id": container_id, "access_token": access_token},
        timeout=60,
    )
    if resp.status_code != 200:
        sys.exit(f"Publish failed: {resp.status_code}\n{resp.text}")
    media_id = resp.json().get("id")
    print(f"  media_id={media_id}")
    return media_id


def fetch_permalink(media_id: str, access_token: str) -> str:
    resp = requests.get(
        f"{GRAPH_BASE}/{media_id}",
        params={"fields": "permalink", "access_token": access_token},
        timeout=30,
    )
    if resp.status_code == 200:
        return resp.json().get("permalink", "")
    return ""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", type=Path, default=None,
                        help="Path to the MP4. Default: latest renders/reels_*.mp4")
    parser.add_argument("--video-url", default=None,
                        help="Skip Drive upload — use this public URL directly.")
    parser.add_argument("--caption", default=None,
                        help="Override caption text. Default: hardcoded for Reel #1.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Upload to Drive + print URL, but don't post to IG.")
    parser.add_argument("--container-id", default=None,
                        help="Act on an EXISTING container (created earlier by "
                             "the reel build) instead of uploading a new video. "
                             "Used by the click-to-publish workflow.")
    parser.add_argument("--check-only", action="store_true",
                        help="With --container-id: print the container's status "
                             "(FINISHED / IN_PROGRESS / ERROR) and exit without "
                             "publishing.")
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")
    access_token = os.getenv("INSTAGRAM_ACCESS_TOKEN")
    ig_user_id = os.getenv("INSTAGRAM_IG_USER_ID")
    if not access_token or not ig_user_id:
        sys.exit("INSTAGRAM_ACCESS_TOKEN and INSTAGRAM_IG_USER_ID must be set in .env")

    # Click-to-publish path: a container already exists (the reel build created
    # it and emailed its id). Check its status, and unless --check-only, publish.
    if args.container_id:
        status_code, detail = check_container_status(args.container_id, access_token)
        print(f"Container {args.container_id}: status={status_code} ({detail})")
        if args.check_only:
            if status_code == "FINISHED":
                print("\n=== READY ===\nContainer is FINISHED and safe to publish.")
            elif status_code in ("IN_PROGRESS", "PUBLISHED"):
                print(f"\n=== {status_code} ===\nNothing to fix; "
                      "re-check shortly or it's already live.")
            else:
                sys.exit(f"\n=== {status_code or 'ERROR'} ===\n{detail}")
            return
        if status_code != "FINISHED":
            sys.exit(f"Refusing to publish — container status is "
                     f"{status_code or 'ERROR'} ({detail}). Nothing was posted.")
        media_id = publish_container(ig_user_id, access_token, args.container_id)
        permalink = fetch_permalink(media_id, access_token)
        print("\n=== PUBLISHED ===")
        print(f"Media ID: {media_id}")
        print(f"Permalink: {permalink}" if permalink
              else "Permalink lookup failed — check your IG profile manually.")
        return

    caption = args.caption or DEFAULT_CAPTION

    if args.video_url:
        video_url = args.video_url
        print(f"Using provided URL: {video_url}")
    else:
        video_path = args.video or latest_render()
        if not video_path.exists():
            sys.exit(f"Video file not found: {video_path}")
        print(f"Source MP4: {video_path}")
        video_url = upload_to_drive(video_path)
        print(f"Public URL: {video_url}")

    if args.dry_run:
        print("\n--dry-run: skipping Instagram publish.")
        print(f"Video URL: {video_url}")
        print("\nCaption:\n" + caption)
        return

    container_id = create_reel_container(ig_user_id, access_token, video_url, caption)
    wait_for_container(container_id, access_token)
    media_id = publish_container(ig_user_id, access_token, container_id)
    permalink = fetch_permalink(media_id, access_token)

    print("\n=== PUBLISHED ===")
    print(f"Media ID: {media_id}")
    if permalink:
        print(f"Permalink: {permalink}")
    else:
        print("Permalink lookup failed — check your IG profile manually.")


if __name__ == "__main__":
    main()
