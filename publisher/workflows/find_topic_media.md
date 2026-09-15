# Workflow — Find Media For a Topic

## Objective

For every new draft row on the "Reels" tab of the Gen Z Capital Google
Sheet, automatically discover the best background **video** + **image**
for that topic and write the URLs back to the row, so the reel renderer
can use real brand-relevant footage instead of generic stock.

## Trigger

`n8n` `repository_dispatch` -> GitHub Actions workflow
`.github/workflows/find_topic_media.yml` -> `python publisher/media_finder.py
--all-pending`.

It can also be run manually from a laptop:

```
python publisher/media_finder.py --row 17
python publisher/media_finder.py --all-pending
python publisher/media_finder.py --row 17 --force   # re-run a row
```

## What the script does

1. Opens the "Reels" worksheet via the existing service account.
2. Picks rows where `Topic` is set **and** `Media Status` is blank or
   `pending`.
3. For each row, runs these searches in parallel:
   - Brand-official YouTube + blog (only if a known brand is mentioned
     in Topic or Key Points — see `publisher/media_sources/brand_detect.py`).
   - General YouTube via `yt-dlp` (no API key).
   - DuckDuckGo image search (proxies Google/Bing).
   - Pexels videos + Pexels photos (existing API key in `.env`).
4. Scores every candidate (`publisher/media_sources/scoring.py`):
   - Brand-official > YouTube > Google image > Pexels.
   - Boosts: brand channel match (+10), 100k+ views (+5), reel-ideal
     duration 3-60s (+5), Google-image hosted on brand domain (+5).
   - Penalties: video longer than 10 min (-10).
5. Writes back to the same row:
   - `Media Video URL` — winning video URL.
   - `Media Image URL` — winning image URL.
   - `Media Source` — `openai_official | youtube | pexels_video | ...`.
   - `Media Backups (JSON)` — next-best candidates for the renderer to
     try if the primary URL fails.
   - `Media Status` — `found` (both winners) / `partial` (one missing) /
     `failed` (nothing found).
   - `Media Found At` — ISO timestamp.

## Required env vars

| Var | Where used |
|---|---|
| `GOOGLE_SHEET_ID` | which spreadsheet to read/write |
| `GOOGLE_SHEET_REELS_NAME` (optional, default `Reels`) | which tab |
| `PEXELS_API_KEY` | Pexels search |
| `google_service_account.json` in repo root | Sheets auth |

YouTube search and DDG image search are **keyless** — no env var needed.

## Adding a new brand

Edit `publisher/media_sources/brand_detect.py`:

```python
BRANDS["newco"] = {
    "aliases": ["newco", "newco-1"],
    "site": "https://newco.ai",
    "blog_index": "https://newco.ai/news",
    "youtube_channel": "@NewcoAI",
    "youtube_handle_id": None,
}
```

That's it — no other file needs to know about the brand. Aliases are
case-insensitive substring matches against the `Topic + Key Points` string.

## Edge cases handled

- **No brand match** -> falls straight through to YouTube / DDG / Pexels.
- **All sources empty** -> `Media Status = failed`, URLs left blank, so
  the existing Pexels-on-the-fly fetch in `reel_generator.py` runs as a
  safety net.
- **Same URL surfaced by two sources** -> de-duped by `media_url`.
- **Re-running a `found` row** -> no-op unless `--force`.
- **A source raises (rate limit, network)** -> warned and skipped; other
  sources still produce candidates.

## How it connects downstream

`publisher/reel_generator.py` checks the row's `Media Video URL` /
`Media Image URL` first. When present, it uses them directly (downloading
via `yt-dlp` when the URL is a YouTube watch URL, plain HTTP otherwise).
When blank, it falls back to its existing Pexels-search behavior.

## Verification

1. `python scripts/setup_reels_sheet.py` — adds the 6 media columns to
   the existing tab. Idempotent.
2. `pip install -r requirements.txt` — installs `yt-dlp`.
3. Add a test row to the Reels tab with `Topic = "ChatGPT voice mode"`,
   leave Media columns blank.
4. `python publisher/media_finder.py --row <row_number>` — should log
   each source's candidate count and the winning sources.
5. Refresh the Sheet — the row now has video URL, image URL,
   `Media Source = openai_official | ...`, `Media Status = found`.
6. Add a generic-topic row ("AI productivity hacks") and re-run —
   confirm `Media Source` is `youtube | ...` or `pexels_video | ...`
   (no brand match).

## Known limitations

- YouTube watch URLs are not direct mp4 links. The renderer must use
  `yt-dlp` to fetch them at render time.
- **YouTube needs a PO token (since 2026-09).** Without one every yt-dlp
  client either has no formats, 403s on the media URL, or returns a
  217 KB stub file. The build runs the `bgutil-ytdlp-pot-provider`
  plugin + its Node server (`publisher/pot_provider.py` starts it on
  demand; the workflows install it; `pip install -U yt-dlp` every run).
  If downloads regress, run `yt-dlp -v URL 2>&1 | grep pot` — it must
  show `bgutil:http` retrieving a gvs token. Details: system_map.md
  "Known traps" → 2026-09-11 entry.
- DuckDuckGo image search occasionally returns empty results when DDG
  rate-limits the IP. Fallback to Pexels handles this.
- og:image scraping for brand blogs is best-effort; if a brand redesigns
  its blog, the regex in `brand_official.py` may need updating.
