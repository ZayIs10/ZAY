"""Research a user-provided topic and write a Draft row to the Google Sheet.

Usage:
    python scripts/research_topic.py "How Gen Z is making $10K/month with AI"
    python scripts/research_topic.py "<topic>" --dry-run         # GPT call only, no Sheet write
    python scripts/research_topic.py "<topic>" --template five_beat   # force template
    python scripts/research_topic.py "<topic>" --skip-enrich    # skip web enrichment

Pipeline:
  1. Enrich the topic with DuckDuckGo news (top 5) + YouTube search (top 3).
  2. One GPT-4o call returns a full reel JSON: caption, headlines, reel_script,
     proof_number, voiceover_lines (with timing), and a chosen template.
  3. Append a row to the Google Sheet with Status='Draft'. The user reviews/edits,
     then flips Status to 'Ready'. `scripts/build_and_publish_reel.py` picks it up.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]

# Reuse the existing SheetsReader from publisher/post_generator.py
sys.path.insert(0, str(REPO_ROOT / "publisher"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("research_topic")


# ---------------------------------------------------------------------------
# Config + env loading
# ---------------------------------------------------------------------------

def load_config() -> dict:
    config_path = REPO_ROOT / "research" / "research_config.json"
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    if os.getenv("OPENAI_API_KEY"):
        config["openai"]["api_key"] = os.getenv("OPENAI_API_KEY")
    if os.getenv("GOOGLE_SHEET_ID"):
        config["google_sheets"]["spreadsheet_id"] = os.getenv("GOOGLE_SHEET_ID")
    if os.getenv("YOUTUBE_API_KEY"):
        config.setdefault("youtube", {})["api_key"] = os.getenv("YOUTUBE_API_KEY")

    # Reels live on their own tab — separate from Sheet1 (posts) and Sheet2
    # (carousels). Override the read/write target so reel rows don't collide.
    config["google_sheets"]["sheet_name"] = os.getenv(
        "GOOGLE_SHEET_REELS_NAME", "Reels",
    )

    config["output_dir"] = str(REPO_ROOT)
    return config


# ---------------------------------------------------------------------------
# Enrichment helpers
# ---------------------------------------------------------------------------

def enrich_with_duckduckgo(topic: str, max_results: int = 5) -> list[dict]:
    """Search DuckDuckGo News for recent stories on the topic."""
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        log.warning("duckduckgo-search not installed; skipping news enrichment.")
        return []
    try:
        with DDGS() as ddgs:
            results = list(ddgs.news(topic, timelimit="m", max_results=max_results))
        log.info("DuckDuckGo: %d news results for %r", len(results), topic)
        return results
    except Exception as exc:
        log.warning("DuckDuckGo failed: %s", exc)
        return []


def enrich_with_youtube(topic: str, api_key: str | None,
                        max_results: int = 3) -> list[dict]:
    """Search YouTube for recent videos. Returns [] if no API key set."""
    if not api_key:
        log.info("No YouTube API key set; skipping video enrichment.")
        return []
    try:
        resp = requests.get(
            "https://www.googleapis.com/youtube/v3/search",
            params={
                "part": "snippet",
                "q": topic,
                "type": "video",
                "maxResults": max_results,
                "order": "relevance",
                "key": api_key,
            },
            timeout=20,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        videos = [
            {
                "title": it["snippet"]["title"],
                "channel": it["snippet"]["channelTitle"],
                "url": f"https://www.youtube.com/watch?v={it['id']['videoId']}",
            }
            for it in items
        ]
        log.info("YouTube: %d videos for %r", len(videos), topic)
        return videos
    except Exception as exc:
        log.warning("YouTube search failed: %s", exc)
        return []


def build_enriched_context(news: list[dict], videos: list[dict]) -> str:
    """Compose a compact context block for the GPT prompt."""
    lines: list[str] = []
    if news:
        lines.append("Recent news:")
        for n in news[:5]:
            title = n.get("title", "").strip()
            source = n.get("source", "").strip() or n.get("url", "")
            lines.append(f"- {title} ({source})")
    if videos:
        lines.append("")
        lines.append("Top YouTube videos:")
        for v in videos:
            lines.append(f"- {v['title']} — {v['channel']}")
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# GPT prompt
# ---------------------------------------------------------------------------

GPT_SYSTEM = (
    "You are a Gen Z Capital scriptwriter for a 30-second 9:16 cinematic Reel "
    "(posted to both YouTube Shorts and Instagram Reels). "
    "Brand = 'your unfair AI advantage': we find the AI tricks that actually work "
    "so the viewer doesn't have to. 'Capital' means leverage, NOT money/finance. "
    "Every reel must teach something the viewer can DO today. "
    "Brand tone: dark, serious, high-stakes. Bold, direct, no fluff. Neon green energy. "
    "No emojis anywhere. Spoken word style — short, fast, punchy. "
    "Output VALID JSON only — no markdown, no commentary."
)

GPT_USER_TEMPLATE = """Topic: {topic}

Enriched context (real data — use specific numbers when relevant):
{context}

Brand tone: {brand_tone}

Produce ONE JSON object with EXACTLY these keys:

{{
  "template": "montage_hook" | "five_beat",
  "post_caption": "<150-200 word IG caption. FIRST sentence must contain the core topic keyword (this is the search signal). End with a FOLLOW CTA that names the specific value of following Gen Z Capital (e.g. 'Follow @genzcapital - the AI tricks worth your time, daily'), NOT 'follow for more'. Then 3-5 hashtags MAX on the final line. No emojis.>",
  "headline_line_1": "<≤24 chars, ALL CAPS, white headline part 1>",
  "headline_line_2": "<≤14 chars, ALL CAPS, NEON GREEN — key number or power word>",
  "headline_line_3": "<≤24 chars, ALL CAPS, white consequence>",
  "subheadline": "<one short gray subhead, max 8 words, no period>",
  "reel_script": "<5 lines separated by \\n. Each ≤ 9 words. Spoken voiceover>",
  "proof_number": "<one short number/percent/dollar figure (e.g. '47%', '$1.4M')>",
  "proof_label": "<≤48 chars, ALL CAPS, what the number means>",
  "reveal_line_1": "<for montage_hook only — ≤14 chars ALL CAPS, like 'ONE NICHE.'>",
  "reveal_line_2": "<for montage_hook only — ≤14 chars ALL CAPS NEON, like 'ONE FORMAT.'>",
  "reveal_line_3": "<for montage_hook only — ≤14 chars ALL CAPS, like 'POSTED DAILY.'>",
  "cliffhanger_count": "<for montage_hook only — like '4 MORE'>",
  "cliffhanger_label": "<for montage_hook only — like 'TRICKS' or 'SECRETS'>",
  "cta_comment_word": "<for montage_hook only — like 'NEXT'>",
  "voiceover_lines": [
    {{"id": "s1", "start": 0.20, "text": "..."}},
    {{"id": "s2", "start": 8.30, "text": "..."}},
    {{"id": "s3", "start": 12.55, "text": "..."}},
    {{"id": "s4", "start": 23.30, "text": "..."}},
    {{"id": "s5", "start": 27.20, "text": "..."}}
  ]
}}

TEMPLATE CHOICE RULES:
- "montage_hook" ONLY when the topic is about Instagram growth, faceless creators,
  social-media strategies, or "look at these accounts/examples" angles. The hook
  shows 6 viral Instagram accounts with follower counts (those visuals stay fixed).
- "five_beat" for everything else (money, AI, productivity, lifestyle, etc.).

VOICEOVER RULES:
- Total ~30 seconds. Each line lands ~0.5s AFTER its on-screen text appears.
- Five lines, in order: hook intro, problem/question, insight, proof/cliffhanger, CTA.
- The 5th (CTA) line is a FOLLOW or LOOP ask - pick whichever fits:
  * FOLLOW (default): name the value of following, e.g. "Follow Gen Z Capital -
    I find these so you don't have to." NEVER bare "follow for more".
  * LOOP (use when the reel taught ONE copy-paste prompt/trick): send them back
    to the start, e.g. "Paste it. Watch the start again if you missed it."
  Do NOT use "comment NEXT" / "DM this" here — for cross-platform (YouTube + IG)
  the ask is follow or loop. (montage_hook keeps its own comment CTA via cta_comment_word.)
- Spoken word style. Contractions OK. Numbers spelled out for TTS clarity
  (e.g. "thirty million" not "30M") EXCEPT in headlines/proof_number which appear on screen.

CAPTION RULES:
- 150-200 words, no emojis.
- FIRST sentence contains the core topic keyword (SEO - outranks hashtags now).
- End with a FOLLOW CTA naming the specific value of following Gen Z Capital
  (the brand = "your unfair AI advantage; I find the AI tricks that work so you
  don't have to"). Example: "Follow @genzcapital for the AI tricks worth your
  time." NEVER bare "follow for more" / "like and subscribe" (IG flags it as bait).
- 3-5 hashtags MAX on the final line (more than 5 risks an IG spam flag).
"""


def call_gpt(client, topic: str, brand_tone: str, context: str, model: str) -> dict:
    """Single GPT-4o call returning the full reel JSON."""
    resp = client.chat.completions.create(
        model=model,
        temperature=0.85,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": GPT_SYSTEM},
            {"role": "user", "content": GPT_USER_TEMPLATE.format(
                topic=topic,
                context=context or "(no enrichment available)",
                brand_tone=brand_tone,
            )},
        ],
    )
    return json.loads(resp.choices[0].message.content)


# ---------------------------------------------------------------------------
# Sheet writing
# ---------------------------------------------------------------------------

REQUIRED_COLUMNS = [
    "Topic", "Key Points", "Brand Tone", "Enriched Context", "YouTube URL",
    "Status", "Published Date", "Post URL", "Instagram Post ID", "Image",
    "Post Type", "Slide Content",
    "Headline Line 1 (White)", "Headline Line 2 (Neon Green)",
    "Headline Line 3 (White)", "Subheadline (Gray)",
    "Key Stat", "Reel Script",
    "Reel Template", "Voiceover Lines (JSON)", "Reel MP4 URL",
]


def ensure_columns(ws) -> list[str]:
    """Make sure all REQUIRED_COLUMNS exist in row 1. Append any missing ones.
    Returns the final ordered list of headers in the sheet."""
    headers = ws.row_values(1)
    missing = [c for c in REQUIRED_COLUMNS if c not in headers]
    if missing:
        log.info("Adding %d missing columns: %s", len(missing), missing)
        new_headers = headers + missing
        # Build the A1 range for header row update — use update_cell loop for safety
        for i, header in enumerate(new_headers, start=1):
            ws.update_cell(1, i, header)
        return new_headers
    return headers


def append_draft_row(reader, payload: dict) -> int:
    """Append a Draft row built from the GPT payload. Returns the new row index."""
    ws = reader.ws
    headers = ensure_columns(ws)

    # Top YouTube URL (if any)
    youtube_url = ""
    if payload.get("_videos"):
        youtube_url = payload["_videos"][0].get("url", "")

    voiceover_json = json.dumps(payload.get("voiceover_lines", []), ensure_ascii=False)

    # Map column name → value
    values: dict[str, Any] = {
        "Topic": payload.get("_topic", ""),
        "Key Points": payload.get("subheadline", ""),
        "Brand Tone": payload.get("_brand_tone", ""),
        "Enriched Context": payload.get("_context", ""),
        "YouTube URL": youtube_url,
        "Status": "Draft",
        "Published Date": "",
        "Post URL": "",
        "Instagram Post ID": "",
        "Image": "",
        "Post Type": payload.get("_post_type", "reel"),
        "Slide Content": "",
        "Headline Line 1 (White)": payload.get("headline_line_1", ""),
        "Headline Line 2 (Neon Green)": payload.get("headline_line_2", ""),
        "Headline Line 3 (White)": payload.get("headline_line_3", ""),
        "Subheadline (Gray)": payload.get("subheadline", ""),
        "Key Stat": payload.get("proof_number", ""),
        "Reel Script": payload.get("reel_script", ""),
        "Reel Template": payload.get("template", "five_beat"),
        "Voiceover Lines (JSON)": voiceover_json,
        "Reel MP4 URL": "",
    }

    row = [values.get(h, "") for h in headers]
    ws.append_row(row, value_input_option="USER_ENTERED")

    # gspread doesn't return the row index — derive from total rows after append
    return len(ws.get_all_values())


def sheet_url(spreadsheet_id: str, row_index: int) -> str:
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit#gid=0&range=A{row_index}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Research a topic and draft a Reel row.")
    parser.add_argument("topic", help="Free-text topic for the reel.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run enrichment + GPT, print the JSON, skip Sheet write.")
    parser.add_argument("--skip-enrich", action="store_true",
                        help="Skip DuckDuckGo + YouTube enrichment.")
    parser.add_argument("--template", choices=["montage_hook", "five_beat", "auto"],
                        default="auto",
                        help="Force a template choice (default: GPT decides).")
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")
    config = load_config()

    api_key = config["openai"].get("api_key")
    if not api_key:
        sys.exit("OPENAI_API_KEY not set in .env")

    from openai import OpenAI
    client = OpenAI(api_key=api_key)

    # Phase 1: enrichment
    if args.skip_enrich:
        news, videos = [], []
    else:
        news = enrich_with_duckduckgo(args.topic, max_results=5)
        videos = enrich_with_youtube(args.topic, config.get("youtube", {}).get("api_key"))
    context = build_enriched_context(news, videos)

    # Phase 1.5: format qualification — is this topic a REEL or a CAROUSEL?
    # Not every topic suits a 30s reel; multi-step how-tos / tool lists /
    # comparisons belong in a swipeable carousel (the @evolving.ai split).
    from format_classifier import classify_format  # type: ignore

    classifier_model = config["openai"].get("model", "gpt-4o-mini")
    fmt = classify_format(
        topic=args.topic,
        key_points=context,
        client=client,
        model=classifier_model,
    )
    log.info("Format: %s (%.0f%% via %s) — %s",
             fmt["format"], fmt["confidence"] * 100,
             fmt["source"], fmt["reason"])

    # Phase 2: GPT call
    log.info("Calling GPT-4o (model=%s)...", config["openai"].get("model", "gpt-4o"))
    payload = call_gpt(
        client,
        topic=args.topic,
        brand_tone=config.get("brand_tone", ""),
        context=context,
        model=config["openai"].get("model", "gpt-4o"),
    )

    if args.template != "auto":
        payload["template"] = args.template

    payload["_topic"] = args.topic
    payload["_brand_tone"] = config.get("brand_tone", "")
    payload["_context"] = context
    payload["_videos"] = videos
    payload["_post_type"] = fmt["format"]
    payload["_format_reason"] = fmt["reason"]
    payload["_generated_at"] = datetime.utcnow().isoformat() + "Z"

    if args.dry_run:
        printable = {k: v for k, v in payload.items() if not k.startswith("_")}
        print(json.dumps(printable, indent=2, ensure_ascii=False))
        return 0

    # Phase 3: write to Sheet
    from post_generator import SheetsReader  # type: ignore

    spreadsheet_id = config["google_sheets"].get("spreadsheet_id")
    if not spreadsheet_id:
        sys.exit("GOOGLE_SHEET_ID not set in .env or research_config.json")

    reader = SheetsReader(config)
    row_index = append_draft_row(reader, payload)

    print()
    print(f"=== DRAFT WRITTEN to row {row_index} ===")
    print(f"Topic:    {args.topic}")
    print(f"Format:   {payload['_post_type'].upper()}  ({payload['_format_reason']})")
    print(f"Template: {payload['template']}")
    print(f"Hook:     {payload.get('headline_line_1', '')} / "
          f"{payload.get('headline_line_2', '')} / "
          f"{payload.get('headline_line_3', '')}")
    print(f"Proof:    {payload.get('proof_number', '')} {payload.get('proof_label', '')}")
    print()
    print("Edit the row in Sheets, then flip Status from Draft to Ready:")
    print(f"  {sheet_url(spreadsheet_id, row_index)}")
    print()
    print("When ready, run:  python scripts/build_and_publish_reel.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
