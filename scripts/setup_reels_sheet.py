"""One-time setup: create the 'Reels' tab on the Gen Z Capital Google Sheet
and populate row 1 with all column headers the reels automation needs.

Run once before using research_topic.py / build_and_publish_reel.py for the
first time. Safe to re-run — it's idempotent.

Usage:
    python scripts/setup_reels_sheet.py             # creates "Reels" tab
    python scripts/setup_reels_sheet.py --tab-name Reels   # custom name
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import gspread
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials

REPO_ROOT = Path(__file__).resolve().parents[1]

# Same column set research_topic.py expects (REQUIRED_COLUMNS there mirrors this).
REELS_COLUMNS = [
    "Topic",
    "Key Points",
    "Brand Tone",
    "Enriched Context",
    "YouTube URL",
    "Status",                       # Draft | Ready | In Progress | Published
    "Published Date",
    "Post URL",
    "Instagram Post ID",
    "Image",
    "Post Type",                    # always "reel" for this tab
    "Slide Content",
    "Headline Line 1 (White)",
    "Headline Line 2 (Neon Green)",
    "Headline Line 3 (White)",
    "Subheadline (Gray)",
    "Key Stat",                     # proof_number (e.g. "47%", "$1.4M")
    "Reel Script",                  # 5 punchy on-screen lines
    "Reel Template",                # montage_hook | five_beat
    "Voiceover Lines (JSON)",       # [{"id":"s1","start":0.2,"text":"..."}, ...]
    "Reel MP4 URL",                 # Drive URL after publish
    # --- Media auto-discovery columns (publisher/media_finder.py) ---
    "Media Video URL",              # best video URL (mp4 or YT watch URL)
    "Media Image URL",              # best image URL (direct image)
    "Media Source",                 # which source won (see media_finder.py)
    "Media Backups (JSON)",         # JSON list of next-best candidates
    "Media Status",                 # pending | found | partial | failed
    "Media Found At",               # ISO timestamp of last finder run
]

SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create + populate the Reels tab on the Gen Z Capital Sheet.",
    )
    parser.add_argument(
        "--tab-name",
        default=os.getenv("GOOGLE_SHEET_REELS_NAME", "Reels"),
        help="Name of the tab to create/update (default: 'Reels' or "
             "$GOOGLE_SHEET_REELS_NAME).",
    )
    parser.add_argument(
        "--rows", type=int, default=200,
        help="Initial row count when creating the tab (default 200).",
    )
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")

    spreadsheet_id = os.getenv("GOOGLE_SHEET_ID")
    if not spreadsheet_id:
        print("ERROR: GOOGLE_SHEET_ID is not set in .env", file=sys.stderr)
        return 1

    creds_path = REPO_ROOT / "google_service_account.json"
    if not creds_path.exists():
        print(f"ERROR: missing {creds_path}", file=sys.stderr)
        return 1

    creds = Credentials.from_service_account_file(str(creds_path), scopes=SCOPES)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(spreadsheet_id)

    # Find or create the tab
    existing_tabs = [ws.title for ws in sh.worksheets()]
    print(f"Existing tabs: {existing_tabs}")

    if args.tab_name in existing_tabs:
        ws = sh.worksheet(args.tab_name)
        print(f"Tab '{args.tab_name}' already exists "
              f"(rows={ws.row_count}, cols={ws.col_count}).")
    else:
        ws = sh.add_worksheet(
            title=args.tab_name,
            rows=str(args.rows),
            cols=str(len(REELS_COLUMNS)),
        )
        print(f"Created tab '{args.tab_name}' "
              f"({args.rows} rows x {len(REELS_COLUMNS)} cols).")

    # Read current row 1
    current_headers = ws.row_values(1)
    print(f"Current row 1 has {len(current_headers)} header(s).")

    # Compute the merged header row: keep any existing extras at the end,
    # add anything missing in the canonical order.
    merged: list[str] = []
    for col in REELS_COLUMNS:
        merged.append(col)
    for col in current_headers:
        if col and col not in merged:
            merged.append(col)

    if merged == current_headers:
        print("All required columns already present. No changes needed.")
        return 0

    # Make sure the sheet has enough columns
    if ws.col_count < len(merged):
        ws.resize(rows=max(ws.row_count, args.rows), cols=len(merged))
        print(f"Resized to {ws.col_count} columns to fit {len(merged)} headers.")

    # Write the headers in row 1 in one batch update for speed + atomicity
    end_col_letter = gspread.utils.rowcol_to_a1(1, len(merged)).rstrip("0123456789")
    target_range = f"A1:{end_col_letter}1"
    ws.update(values=[merged], range_name=target_range,
              value_input_option="USER_ENTERED")
    print(f"Wrote {len(merged)} headers to {target_range}.")

    # Format header row: bold + dark background + white text
    try:
        ws.format(
            target_range,
            {
                "backgroundColor": {"red": 0.1, "green": 0.1, "blue": 0.1},
                "textFormat": {
                    "bold": True,
                    "foregroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0},
                },
            },
        )
        ws.freeze(rows=1)
        print("Styled + froze the header row.")
    except Exception as exc:
        # Formatting is nice-to-have; don't fail if the API blocks it
        print(f"WARN: header formatting skipped: {exc}")

    print()
    print("=" * 60)
    print(f"OK — tab '{args.tab_name}' is ready.")
    print(f"Spreadsheet: https://docs.google.com/spreadsheets/d/"
          f"{spreadsheet_id}/edit")
    print()
    print("Columns:")
    for i, col in enumerate(merged, start=1):
        marker = "  +" if col in REELS_COLUMNS else "   "
        print(f"{marker} {i:>2}. {col}")
    print()
    print("Next: python scripts/research_topic.py \"<your topic>\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
