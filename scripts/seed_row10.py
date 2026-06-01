"""One-shot: seed Reels row 10 with a real Topic + Post Caption so Workflow B has data."""
from pathlib import Path
import os
import gspread
from dotenv import load_dotenv

load_dotenv()

SHEET_ID = os.environ["GOOGLE_SHEET_ID"]
SHEET_NAME = os.environ.get("GOOGLE_SHEET_REELS_NAME", "Reels")
SA_PATH = Path(__file__).resolve().parent.parent / "google_service_account.json"

TOPIC = "How to use Claude for coding"
POST_CAPTION = (
    "Claude is the easiest way to ship code without writing it yourself.\n"
    "Open Claude.ai or install Claude Code in your terminal.\n"
    "Paste your goal — \"build me a Python script that does X\".\n"
    "It writes, runs, and debugs the code for you.\n"
    "Refine with plain English: \"add error handling\", \"make it faster\".\n"
    "Beginners ship working apps in one afternoon.\n"
    "We only post the best AI tools & how-tos"
)

gc = gspread.service_account(filename=str(SA_PATH))
sh = gc.open_by_key(SHEET_ID)
ws = sh.worksheet(SHEET_NAME)

header = ws.row_values(1)
def col(name):
    return header.index(name) + 1

row = 10
ws.update_cell(row, col("Topic"), TOPIC)
ws.update_cell(row, col("Post Caption"), POST_CAPTION)
ws.update_cell(row, col("Status"), "Ready")

print(f"Seeded row {row}: Topic + Post Caption + Status=Ready")
print(f"  Topic: {TOPIC}")
print(f"  Caption lines: {len(POST_CAPTION.splitlines())}")
