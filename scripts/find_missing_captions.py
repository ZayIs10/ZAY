"""Read-only audit: list rows in the Reels sheet that are missing a Post Caption.

Run:  python scripts/find_missing_captions.py
"""
import os
import sys
from pathlib import Path

import gspread
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials

REPO_ROOT = Path(__file__).resolve().parents[1]
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def open_ws():
    load_dotenv(REPO_ROOT / ".env")
    sheet_id = os.getenv("GOOGLE_SHEET_ID")
    if not sheet_id:
        sys.exit("GOOGLE_SHEET_ID is not set in .env")
    sheet_name = os.getenv("GOOGLE_SHEET_REELS_NAME", "Reels")
    creds = Credentials.from_service_account_file(
        str(REPO_ROOT / "google_service_account.json"), scopes=SCOPES,
    )
    gc = gspread.authorize(creds)
    return gc.open_by_key(sheet_id).worksheet(sheet_name)


def main():
    ws = open_ws()
    rows = ws.get_all_records()  # list of dicts keyed by header row
    cap_col = "Post Caption"

    missing = []
    for i, row in enumerate(rows, start=2):  # row 1 is the header
        topic = str(row.get("Topic", "")).strip()
        caption = str(row.get(cap_col, "")).strip()
        status = str(row.get("Status", "")).strip()
        if topic and not caption:
            missing.append((i, status, topic))

    if not missing:
        print("All rows with a Topic already have a Post Caption.")
        return

    print(f"{len(missing)} row(s) missing '{cap_col}':\n")
    print(f"{'ROW':>4}  {'STATUS':<16}  TOPIC")
    print("-" * 70)
    for row_idx, status, topic in missing:
        print(f"{row_idx:>4}  {status:<16}  {topic}")


if __name__ == "__main__":
    main()
