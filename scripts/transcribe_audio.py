"""Quickly transcribe a single MP3 via OpenAI Whisper."""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(REPO_ROOT / ".env")

audio = Path(sys.argv[1]).resolve()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
with open(audio, "rb") as f:
    r = client.audio.transcriptions.create(
        model="whisper-1",
        file=f,
        response_format="verbose_json",
        timestamp_granularities=["segment"],
    )
print("FULL TEXT:")
print(r.text)
print("\nSEGMENTS:")
for s in r.segments:
    print(f"  [{s.start:5.2f} - {s.end:5.2f}] {s.text.strip()}")
