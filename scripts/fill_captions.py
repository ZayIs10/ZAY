"""Generate + write a Post Caption (with hashtags) for Reels rows.

Reuses the publisher's CaptionGenerator (GPT) so captions match brand voice,
appends the standard hashtag set from research_config.json, then writes the
result back to the 'Post Caption' column of the Reels sheet.

Usage:
    python scripts/fill_captions.py 14 22                 # those rows
    python scripts/fill_captions.py --all-empty           # every empty caption
    python scripts/fill_captions.py --all-empty --dry-run # preview, don't write
    python scripts/fill_captions.py 14 22 --add-hashtags-only  # just add tags
    python scripts/fill_captions.py 22 --overwrite        # regenerate existing
"""
import argparse
import os
import sys
from pathlib import Path

import gspread
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials
from openai import OpenAI

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "publisher"))  # post_generator imports siblings flat

from post_generator import CaptionGenerator, load_config  # noqa: E402

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
CAPTION_COL = "Post Caption"


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


def read_row(ws, headers, row_index):
    raw = ws.row_values(row_index)
    return {h: (raw[i] if i < len(raw) else "") for i, h in enumerate(headers)}


def append_hashtags(caption: str, hashtags: str) -> str:
    """Append config hashtags as a trailing line, skipping any already
    present. No-op if hashtags is empty."""
    if not hashtags.strip():
        return caption
    have = {t.lower() for t in caption.split() if t.startswith("#")}
    new_tags = [t for t in hashtags.split() if t.lower() not in have]
    if not new_tags:
        return caption
    return f"{caption.rstrip()}\n\n{' '.join(new_tags)}"


def caption_has_hashtags(caption: str) -> bool:
    return any(t.startswith("#") for t in caption.split())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("rows", nargs="*", type=int,
                    help="1-indexed sheet rows (ignored with --all-empty)")
    ap.add_argument("--all-empty", action="store_true",
                    help="target every row with a Topic but empty Post Caption")
    ap.add_argument("--dry-run", action="store_true",
                    help="generate + print captions but don't write")
    ap.add_argument("--overwrite", action="store_true",
                    help="regenerate even if a caption already exists")
    ap.add_argument("--add-hashtags-only", action="store_true",
                    help="don't call GPT; just append config hashtags to "
                         "existing captions that lack them")
    args = ap.parse_args()

    ws = open_ws()
    headers = ws.row_values(1)
    if CAPTION_COL not in headers:
        sys.exit(f"Column {CAPTION_COL!r} not found in sheet headers.")
    cap_col_idx = headers.index(CAPTION_COL) + 1  # gspread is 1-indexed

    config = load_config()
    hashtags = config.get("instagram", {}).get("hashtags", "")

    # Resolve the target row list.
    if args.all_empty:
        all_values = ws.get_all_values()
        t_idx = headers.index("Topic")
        targets = []
        for r_i, raw in enumerate(all_values[1:], start=2):
            topic = (raw[t_idx] if t_idx < len(raw) else "").strip()
            cap = (raw[cap_col_idx - 1] if cap_col_idx - 1 < len(raw) else "").strip()
            if topic and not cap:
                targets.append(r_i)
        print(f"--all-empty -> {len(targets)} row(s): {targets}")
    else:
        targets = args.rows
    if not targets:
        sys.exit("No target rows. Pass row numbers or --all-empty.")

    # --add-hashtags-only needs no GPT client.
    gen = None
    if not args.add_hashtags_only:
        if not config["openai"].get("api_key"):
            sys.exit("OPENAI_API_KEY is not set in .env")
        client = OpenAI(api_key=config["openai"]["api_key"])
        gen = CaptionGenerator(config, client)

    for row_index in targets:
        row = read_row(ws, headers, row_index)
        topic = (row.get("Topic") or "").strip()
        existing = (row.get(CAPTION_COL) or "").strip()

        if not topic:
            print(f"Row {row_index}: no Topic — skipping.")
            continue

        # Mode 1: just patch hashtags onto an existing caption.
        if args.add_hashtags_only:
            if not existing:
                print(f"Row {row_index}: no caption to patch — skipping.")
                continue
            if caption_has_hashtags(existing):
                print(f"Row {row_index}: already has hashtags — skipping.")
                continue
            caption = append_hashtags(existing, hashtags)
            print(f"\nRow {row_index}: appending hashtags.\n"
                  f"--- caption ---\n{caption}\n---------------")
            if not args.dry_run:
                ws.update_cell(row_index, cap_col_idx, caption)
                print(f"Row {row_index}: hashtags written.")
            else:
                print(f"Row {row_index}: DRY RUN — not written.")
            continue

        # Mode 2: generate a caption (+ hashtags).
        if existing and not args.overwrite:
            print(f"Row {row_index}: already has a caption — skipping "
                  f"(use --overwrite to replace).")
            continue

        print(f"\nRow {row_index}: generating caption for {topic!r} ...")
        result = gen.generate(row)
        caption = (result.get("post_caption") or "").strip()
        if not caption:
            print(f"Row {row_index}: GPT returned no caption — skipping.")
            continue
        caption = append_hashtags(caption, hashtags)

        print(f"--- caption ---\n{caption}\n---------------")
        if args.dry_run:
            print(f"Row {row_index}: DRY RUN — not written.")
            continue

        ws.update_cell(row_index, cap_col_idx, caption)
        print(f"Row {row_index}: written to '{CAPTION_COL}'.")

    print("\nDone.")


if __name__ == "__main__":
    main()
