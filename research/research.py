"""
Gen Z Automation - Instagram Research Pipeline
Week 1 script: runs every Sunday.

Flow:
  1. Aggregate trending signals from Reddit, HackerNews, RSS, Google Trends, NewsAPI (all FREE)
  2. Analyze CTA patterns and hook structures
  3. Generate 7 topic ideas via GPT-4o
  4. Enrich each topic with web research (DuckDuckGo news + YouTube)
  5. Write enriched topics to Google Sheets

Exit codes: 0=success  1=GPT failure  2=data fetch error  3=Sheets error
"""

import json
import logging
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from difflib import SequenceMatcher

import feedparser
import gspread
import requests
from dotenv import load_dotenv
from duckduckgo_search import DDGS
from google.oauth2.service_account import Credentials
from openai import OpenAI
from pytrends.request import TrendReq

load_dotenv()


# ---------------------------------------------------------------------------
# Config & Logging
# ---------------------------------------------------------------------------

def load_config(config_path: str = None) -> dict:
    if config_path is None:
        config_path = os.path.join(os.path.dirname(__file__), "research_config.json")
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    # Always resolve output_dir relative to this file's location (cross-platform)
    config["output_dir"] = os.path.abspath(
        os.path.join(os.path.dirname(__file__), ".."))

    # Override config with environment variables (keeps secrets out of JSON)
    if os.getenv("OPENAI_API_KEY"):
        config["openai"]["api_key"] = os.getenv("OPENAI_API_KEY")
    if os.getenv("NEWSAPI_KEY"):
        config["free_sources"]["newsapi"]["api_key"] = os.getenv("NEWSAPI_KEY")
    if os.getenv("YOUTUBE_API_KEY"):
        config["youtube"]["api_key"] = os.getenv("YOUTUBE_API_KEY")
    if os.getenv("GOOGLE_SHEET_ID"):
        config["google_sheets"]["spreadsheet_id"] = os.getenv("GOOGLE_SHEET_ID")

    return config


def setup_logging(config: dict) -> None:
    log_path = os.path.join(config["output_dir"], config.get("log_file", "research_log.txt"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


# ---------------------------------------------------------------------------
# Phase 1: Free Source Aggregator (replaces Apify)
# ---------------------------------------------------------------------------

class FreeSourceAggregator:
    """Fetches trending finance/wealth/AI content signals from 5 free sources in parallel."""

    def __init__(self, config: dict):
        self.cfg = config["free_sources"]

    # -- public --

    def fetch_all(self) -> dict:
        sources = {
            "reddit":     self._fetch_reddit,
            "hackernews": self._fetch_hackernews,
            "rss":        self._fetch_rss_feeds,
            "trends":     self._fetch_google_trends,
            "newsapi":    self._fetch_newsapi,
        }
        results = {}
        with ThreadPoolExecutor(max_workers=5) as ex:
            futures = {ex.submit(fn): name for name, fn in sources.items()}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    results[name] = future.result()
                    logging.info(f"  {name}: {len(results[name])} items")
                except Exception as e:
                    logging.warning(f"  Source {name} failed: {e}")
                    results[name] = []
        return results

    # -- private fetch methods --

    def _fetch_reddit(self) -> list:
        cfg = self.cfg["reddit"]
        posts = []
        headers = {"User-Agent": cfg["user_agent"]}
        for sub in cfg["subreddits"]:
            try:
                url = f"https://www.reddit.com/r/{sub}/hot.json?limit={cfg['posts_per_subreddit']}"
                resp = requests.get(url, headers=headers, timeout=15)
                if resp.status_code != 200:
                    logging.warning(f"    Reddit r/{sub}: HTTP {resp.status_code}")
                    continue
                children = resp.json().get("data", {}).get("children", [])
                for child in children:
                    d = child.get("data", {})
                    caption = d.get("title", "") + " " + (d.get("selftext", "") or "")[:400]
                    score   = d.get("score", 0)
                    posts.append(self._normalize(
                        caption, score, d.get("num_comments", 0),
                        [d.get("link_flair_text", sub)], d.get("created_utc", "")
                    ))
            except Exception as e:
                logging.warning(f"    Reddit r/{sub}: {e}")
            time.sleep(1)
        return posts

    def _fetch_hackernews(self) -> list:
        cfg = self.cfg["hackernews"]
        posts = []
        for kw in cfg["keywords"]:
            try:
                url = (
                    f"https://hn.algolia.com/api/v1/search"
                    f"?tags=story&query={requests.utils.quote(kw)}"
                    f"&hitsPerPage={cfg['hits_per_keyword']}"
                )
                hits = requests.get(url, timeout=15).json().get("hits", [])
                for h in hits:
                    caption = h.get("title", "") + " " + (h.get("story_text") or "")[:300]
                    posts.append(self._normalize(
                        caption, h.get("points", 0), h.get("num_comments", 0),
                        [kw.replace(" ", "_")], h.get("created_at", "")
                    ))
            except Exception as e:
                logging.warning(f"    HN keyword '{kw}': {e}")
            time.sleep(0.5)
        return posts

    def _fetch_rss_feeds(self) -> list:
        feeds = self.cfg["rss_feeds"]
        posts = []
        for feed_url in feeds:
            try:
                parsed = feedparser.parse(feed_url)
                for entry in parsed.entries[:15]:
                    title   = entry.get("title", "")
                    summary = entry.get("summary", "")[:400]
                    caption = title + " " + summary
                    # derive topic tags from title words
                    tags = [w.lower() for w in title.split() if len(w) > 4][:5]
                    posts.append(self._normalize(caption, 0, 0, tags, entry.get("published", "")))
            except Exception as e:
                logging.warning(f"    RSS {feed_url}: {e}")
        return posts

    def _fetch_google_trends(self) -> list:
        cfg = self.cfg["google_trends"]
        posts = []
        try:
            pytrends = TrendReq(hl="en-US", tz=360)
            keywords = cfg["keywords"]
            # Process in batches of 1 to avoid quota issues
            for kw in keywords:
                try:
                    pytrends.build_payload([kw], timeframe=cfg["timeframe"], geo=cfg["geo"])
                    related = pytrends.related_queries()
                    rising_df = related.get(kw, {}).get("rising")
                    if rising_df is not None and not rising_df.empty:
                        for _, row in rising_df.head(5).iterrows():
                            query = str(row.get("query", ""))
                            value = int(row.get("value", 0))
                            caption = f"Trending search: {query} — rising {value}% this week"
                            posts.append(self._normalize(
                                caption, value, 0,
                                [query.replace(" ", "_")], datetime.utcnow().isoformat()
                            ))
                except Exception as e:
                    logging.warning(f"    Trends keyword '{kw}': {e}")
                time.sleep(2)
        except Exception as e:
            logging.warning(f"    Google Trends init failed: {e}")
        return posts

    def _fetch_newsapi(self) -> list:
        cfg = self.cfg["newsapi"]
        api_key = cfg.get("api_key", "")
        if not api_key:
            logging.info("    NewsAPI key not configured — skipping")
            return []
        posts = []
        for query in cfg["queries"]:
            try:
                resp = requests.get(
                    "https://newsapi.org/v2/everything",
                    params={
                        "q": query, "sortBy": "popularity",
                        "pageSize": cfg["page_size"], "apiKey": api_key,
                    },
                    timeout=15,
                )
                articles = resp.json().get("articles", [])
                for a in articles:
                    caption = (a.get("title") or "") + " " + (a.get("description") or "")
                    tags    = [query.replace(" ", "_")]
                    posts.append(self._normalize(caption, 0, 0, tags, a.get("publishedAt", "")))
            except Exception as e:
                logging.warning(f"    NewsAPI query '{query}': {e}")
            time.sleep(0.5)
        return posts

    # -- shared normalizer --

    @staticmethod
    def _normalize(caption: str, likes: int, comments: int,
                   hashtags: list, timestamp: str) -> dict:
        return {
            "id":             "",
            "caption":        caption.strip(),
            "likes":          max(0, int(likes)),
            "comments":       max(0, int(comments)),
            "timestamp":      str(timestamp),
            "hashtags":       [str(t) for t in hashtags if t],
            "is_video":       False,
            "engagement_rate": round(int(likes) / 1000, 4),
        }


# ---------------------------------------------------------------------------
# Phase 2: Content Analyzer (UNCHANGED)
# ---------------------------------------------------------------------------

class ContentAnalyzer:
    CTA_PATTERNS = {
        "dm_ask":       re.compile(r"DM\s+me|send\s+me\s+a\s+DM|message\s+me", re.IGNORECASE),
        "link_bio":     re.compile(r"link\s+in\s+bio|check\s+(my\s+)?bio|bio\s+link", re.IGNORECASE),
        "comment_hook": re.compile(r"comment\s+[\"']?\w+[\"']?\s+(below|if|to|and)", re.IGNORECASE),
        "save_this":    re.compile(r"save\s+this|bookmark\s+this", re.IGNORECASE),
        "share_worthy": re.compile(r"share\s+this|send\s+this\s+to|tag\s+someone", re.IGNORECASE),
    }
    HOOK_PATTERNS = {
        "number_stat": re.compile(r"\b\d+[\d,]*\s*%|\b\d+[\d,]+\s+\w+", re.IGNORECASE),
        "question":    re.compile(r"\?"),
        "bold_claim":  re.compile(r"\b(never|always|most|every|no one|secret|truth|reality)\b", re.IGNORECASE),
        "pain_point":  re.compile(r"\b(fail|broke|struggle|wrong|mistake|problem|stop|quit)\b", re.IGNORECASE),
        "contrarian":  re.compile(r"\b(actually|contrary|opposite|wrong|myth|lie|false)\b", re.IGNORECASE),
    }

    def build_pattern_report(self, raw_data: dict) -> dict:
        all_posts = [p for posts in raw_data.values() for p in posts]
        cta_stats  = {k: [] for k in self.CTA_PATTERNS}
        hook_stats = {k: [] for k in self.HOOK_PATTERNS}
        topic_freq: dict = {}

        for post in all_posts:
            caption = post["caption"]
            for name, pat in self.CTA_PATTERNS.items():
                if pat.search(caption):
                    cta_stats[name].append(post["engagement_rate"])
            for name, pat in self.HOOK_PATTERNS.items():
                if pat.search(caption):
                    hook_stats[name].append(post["likes"])
            for tag in post.get("hashtags", []):
                t = tag.lower().lstrip("#")
                topic_freq[t] = topic_freq.get(t, 0) + 1

        top_cta = sorted(
            [{"pattern_type": k, "avg_engagement_rate": round(sum(v)/len(v), 4), "frequency": len(v)}
             for k, v in cta_stats.items() if v],
            key=lambda x: x["avg_engagement_rate"], reverse=True
        )[:5]

        top_hooks = sorted(
            [{"structure_type": k, "avg_likes": int(sum(v)/len(v)), "frequency": len(v)}
             for k, v in hook_stats.items() if v],
            key=lambda x: x["avg_likes"], reverse=True
        )[:5]

        return {
            "top_cta_patterns":    top_cta,
            "top_hook_structures": top_hooks,
            "top_topics":          sorted(topic_freq, key=topic_freq.get, reverse=True)[:15],
            "accounts_analyzed":   len(raw_data),
            "posts_analyzed":      len(all_posts),
            "run_timestamp":       datetime.utcnow().isoformat(),
        }

    def build_analysis_prompt(self, report: dict) -> str:
        lines = [
            "ACCOUNT ANALYSIS SUMMARY",
            f"Sources: {report['accounts_analyzed']} | Posts: {report['posts_analyzed']}",
            "",
            "TOP CTA PATTERNS (by avg engagement rate):",
        ]
        for i, p in enumerate(report["top_cta_patterns"], 1):
            lines.append(f"{i}. {p['pattern_type']}: {p['avg_engagement_rate']}% avg ER, {p['frequency']} posts")
        lines += ["", "TOP HOOK STRUCTURES (by avg likes):"]
        for i, h in enumerate(report["top_hook_structures"], 1):
            lines.append(f"{i}. {h['structure_type']}: avg {h['avg_likes']:,} likes, {h['frequency']} posts")
        lines += ["", "TOP HASHTAG TOPICS:", ", ".join(report["top_topics"])]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Phase 3: Topic Generator (UNCHANGED)
# ---------------------------------------------------------------------------

class TopicGenerator:
    def __init__(self, config: dict, client: OpenAI):
        self.client      = client
        self.model       = config["openai"]["model"]
        self.temperature = config["openai"]["temperature"]
        self.n           = config["topics_per_run"]
        self.brand_tone  = config["brand_tone"]

    def generate_topics(self, pattern_report: dict) -> list:
        summary = ContentAnalyzer().build_analysis_prompt(pattern_report)
        n_carousel = max(1, int(self.n * 0.7))
        n_single   = self.n - n_carousel

        carousel_topics = self._call(summary, n_carousel, post_type="carousel", strict=False)
        if carousel_topics is None:
            logging.warning("Carousel topic generation failed, retrying strict...")
            carousel_topics = self._call(summary, n_carousel, post_type="carousel", strict=True)
        if carousel_topics is None:
            carousel_topics = []

        single_topics = self._call(summary, n_single, post_type="single", strict=False)
        if single_topics is None:
            single_topics = []

        topics = carousel_topics + single_topics
        if not topics:
            logging.error("GPT-4o topic generation failed both attempts.")
            sys.exit(1)
        return topics

    def _call(self, summary: str, n: int, post_type: str = "single",
              strict: bool = False) -> list | None:
        strict_note = f" Return EXACTLY {n} items." if strict else ""

        if post_type == "carousel":
            system_msg = (
                f"You are a Gen Z content strategist. Output a JSON array of exactly {n} objects."
                + strict_note
            )
            user_msg = (
                f"Based on this analysis:\n\n{summary}\n\n"
                f"Brand tone: {self.brand_tone}\n\n"
                f"Generate exactly {n} Instagram CAROUSEL topic ideas for wealth/AI/finance.\n\n"
                f"Return a JSON array of {n} objects, each with keys:\n"
                f"- topic (max 10 words)\n"
                f"- key_points (EXACTLY 5 distinct sub-points, comma-separated, for 5 content slides)\n"
                f"- suggested_stat (one powerful statistic string with a number, e.g. '47% of Gen Z...')\n"
                f"- cta_pattern_used\n"
                f"- hook_structure_used\n\n"
                f"Return ONLY the JSON array."
            )
        else:
            system_msg = (
                f"You are a Gen Z content strategist. Output a JSON array of exactly {n} objects."
                + strict_note
            )
            user_msg = (
                f"Based on this analysis:\n\n{summary}\n\n"
                f"Brand tone: {self.brand_tone}\n\n"
                f"Generate exactly {n} Instagram single-image post topic ideas.\n\n"
                f"Return a JSON array of {n} objects, each with keys:\n"
                f"- topic (max 10 words)\n"
                f"- key_points (3-4 points, comma-separated)\n"
                f"- cta_pattern_used\n"
                f"- hook_structure_used\n\n"
                f"Return ONLY the JSON array."
            )

        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                temperature=self.temperature,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user",   "content": user_msg},
                ],
            )
            parsed = json.loads(resp.choices[0].message.content)
            if isinstance(parsed, dict):
                for v in parsed.values():
                    if isinstance(v, list):
                        parsed = v
                        break
            if not isinstance(parsed, list):
                return None
            required = {"topic", "key_points", "cta_pattern_used", "hook_structure_used"}
            valid = [t for t in parsed if required.issubset(t.keys())]
            for t in valid:
                t["post_type"] = post_type
            return valid[:n] if len(valid) >= n else None
        except Exception as e:
            logging.warning(f"GPT-4o call error: {e}")
            return None


# ---------------------------------------------------------------------------
# Phase 4: Free Web Enricher (replaces Serper-based WebEnricher)
# ---------------------------------------------------------------------------

class FreeWebEnricher:
    """Enriches topics using DuckDuckGo news (free), NewsAPI fallback, RSS cache, and YouTube."""

    def __init__(self, config: dict, rss_cache: list = None):
        self.youtube_key = config["youtube"]["api_key"]
        self.newsapi_key = config["free_sources"]["newsapi"].get("api_key", "")
        self.ddg_cfg     = config["free_sources"]["duckduckgo"]
        self.newsapi_cfg = config["free_sources"]["newsapi"]
        # rss_cache is the list of RSS posts already fetched in Phase 1 — zero extra network calls
        self.rss_cache   = rss_cache or []

    def enrich(self, topic: str) -> dict:
        articles = []

        # Primary: DuckDuckGo news (free, no key)
        articles += self._duckduckgo_news(topic)
        time.sleep(1)

        # Secondary: NewsAPI if thin
        if len(articles) < 3 and self.newsapi_key:
            articles += self._newsapi_enrich(topic)

        # Tertiary: scan RSS cache
        if len(articles) < 2:
            articles += self._rss_cache_search(topic)

        youtube   = self._youtube_search(topic)
        key_stats = self._extract_stats(articles)
        context   = self._build_context(topic, articles, youtube, key_stats)
        return {
            "enriched_context": context,
            "youtube_url":      youtube.get("url", "") if youtube else "",
        }

    def _duckduckgo_news(self, topic: str) -> list:
        try:
            results = DDGS().news(
                keywords=topic,
                region="wt-wt",
                safesearch="off",
                timelimit=self.ddg_cfg.get("timelimit", "w"),
                max_results=self.ddg_cfg.get("max_results_per_topic", 5),
            )
            return [
                {"title": r.get("title", ""), "snippet": r.get("body", ""),
                 "source": r.get("source", ""), "url": r.get("url", "")}
                for r in (results or [])
            ]
        except Exception as e:
            logging.warning(f"DuckDuckGo news failed for '{topic}': {e}")
            return []

    def _newsapi_enrich(self, topic: str) -> list:
        try:
            resp = requests.get(
                "https://newsapi.org/v2/everything",
                params={
                    "q": topic, "sortBy": "relevancy",
                    "pageSize": 3, "apiKey": self.newsapi_key,
                },
                timeout=15,
            )
            return [
                {"title": a.get("title", ""), "snippet": a.get("description", ""),
                 "source": (a.get("source") or {}).get("name", ""), "url": a.get("url", "")}
                for a in resp.json().get("articles", [])
            ]
        except Exception as e:
            logging.warning(f"NewsAPI enrich failed for '{topic}': {e}")
            return []

    def _rss_cache_search(self, topic: str) -> list:
        """Keyword-match already-fetched RSS entries — zero network calls."""
        topic_words = set(topic.lower().split())
        matches = []
        for post in self.rss_cache:
            caption_words = set(post["caption"].lower().split())
            overlap = len(topic_words & caption_words) / max(len(topic_words), 1)
            if overlap >= 0.3:
                matches.append({
                    "title":   post["caption"][:80],
                    "snippet": post["caption"][80:300],
                    "source":  "RSS",
                    "url":     "",
                })
        return matches[:3]

    def _youtube_search(self, topic: str) -> dict | None:
        if not self.youtube_key:
            return None
        try:
            resp = requests.get(
                "https://www.googleapis.com/youtube/v3/search",
                params={
                    "part": "snippet", "q": topic, "type": "video",
                    "maxResults": 1, "order": "relevance", "key": self.youtube_key,
                },
                timeout=15,
            )
            items = resp.json().get("items", [])
            if not items:
                return None
            item = items[0]
            video_id = item.get("id", {}).get("videoId", "")
            return {
                "title":   item["snippet"].get("title", ""),
                "url":     f"https://www.youtube.com/watch?v={video_id}",
                "channel": item["snippet"].get("channelTitle", ""),
            }
        except Exception as e:
            logging.warning(f"YouTube search failed for '{topic}': {e}")
            return None

    @staticmethod
    def _extract_stats(articles: list) -> list:
        text = " ".join(a["snippet"] for a in articles)
        patterns = [
            re.compile(r"\d+(?:\.\d+)?%"),
            re.compile(r"\$[\d,]+(?:\.\d+)?(?:\s?[BMKTbmkt](?:illion|rillion)?)?"),
            re.compile(r"\b\d{1,3}(?:,\d{3})+\s+[a-z]+", re.IGNORECASE),
        ]
        stats = []
        for pat in patterns:
            stats.extend(pat.findall(text))
        return list(dict.fromkeys(stats))[:8]

    @staticmethod
    def _build_context(topic: str, articles: list, youtube, key_stats: list) -> str:
        month_year = datetime.utcnow().strftime("%B %Y")
        lines = [f'Recent news on "{topic}" (as of {month_year}):', ""]
        for a in articles[:3]:
            if a["snippet"]:
                words = a["snippet"].split()
                snippet = " ".join(words[:80]) + ("..." if len(words) > 80 else "")
                lines.append(f"Source: {a['source'] or 'Unknown'} — {snippet}")
                lines.append("")
        if key_stats:
            lines.append("Key statistics:")
            for s in key_stats[:5]:
                lines.append(f"- {s}")
            lines.append("")
        if youtube and youtube.get("title"):
            lines.append(f'Top YouTube resource: "{youtube["title"]}" by {youtube["channel"]}')
            lines.append(f"URL: {youtube['url']}")
        return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# Phase 5: Google Sheets Writer (UNCHANGED)
# ---------------------------------------------------------------------------

class GoogleSheetsWriter:
    SCOPES = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]

    def __init__(self, config: dict):
        self.cfg        = config["google_sheets"]
        self.brand_tone = config["brand_tone"]
        self.output_dir = config["output_dir"]
        # Compute credentials path relative to this file (works on any OS)
        creds_path = os.path.abspath(os.path.join(
            os.path.dirname(__file__), "..", self.cfg["credentials_file"]))
        creds = Credentials.from_service_account_file(creds_path, scopes=self.SCOPES)
        self.gc = gspread.authorize(creds)

    def ensure_columns_exist(self, ws, required_columns: list) -> None:
        existing_headers = ws.row_values(1)
        missing = [c for c in required_columns if c not in existing_headers]
        if not missing:
            return
        next_col = len(existing_headers) + 1
        for i, col_name in enumerate(missing):
            ws.update_cell(1, next_col + i, col_name)
            logging.info(f"  Added missing column: {col_name}")

    def append_topics(self, topics: list) -> int:
        try:
            sh = self.gc.open_by_key(self.cfg["spreadsheet_id"])
            ws = sh.worksheet(self.cfg["sheet_name"])
        except Exception as e:
            logging.error(f"Google Sheets open failed: {e}")
            sys.exit(3)

        self.ensure_columns_exist(ws, self.cfg.get("columns", []))

        existing = ws.col_values(1)[1:]
        rows, skipped = [], 0

        for t in topics:
            if self._is_duplicate(t["topic"], existing):
                logging.info(f"  Skipping duplicate: {t['topic']}")
                skipped += 1
                continue
            post_type = t.get("post_type", "single")
            rows.append([
                t["topic"],
                t.get("key_points", ""),
                self.brand_tone,
                t.get("enriched_context", ""),
                t.get("youtube_url", ""),
                "Ready",
                "",
                "",
                "",
                "",
                post_type,
                "",
            ])
            existing.append(t["topic"])

        if not rows:
            logging.warning("All topics were duplicates — nothing written.")
            return 0

        try:
            ws.append_rows(rows, value_input_option="USER_ENTERED")
            logging.info(f"  Wrote {len(rows)} rows ({skipped} duplicates skipped).")
        except Exception as e:
            logging.error(f"append_rows failed: {e}")
            sys.exit(3)

        return len(rows)

    @staticmethod
    def _is_duplicate(new_topic: str, existing: list, threshold: float = 0.85) -> bool:
        new_lower = new_topic.lower()
        return any(
            SequenceMatcher(None, new_lower, t.lower()).ratio() >= threshold
            for t in existing
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Gen Z Research Pipeline")
    parser.add_argument("--count", type=int, default=None,
                        help="Number of topics to generate (overrides config topics_per_run)")
    args = parser.parse_args()

    config = load_config()
    if args.count is not None:
        config["topics_per_run"] = args.count
    setup_logging(config)
    logging.info("=== Gen Z Research Pipeline starting (free sources) ===")

    # Validate required keys
    for key, val in [
        ("OpenAI key",  config["openai"]["api_key"]),
        ("YouTube key", config["youtube"]["api_key"]),
        ("Sheet ID",    config["google_sheets"]["spreadsheet_id"]),
    ]:
        if not val or "YOUR_" in val:
            logging.warning(f"{key} not configured — some features may be limited.")

    # Phase 1: Aggregate free sources in parallel
    aggregator  = FreeSourceAggregator(config)
    raw_data    = aggregator.fetch_all()
    posts_found = sum(len(v) for v in raw_data.values())
    logging.info(
        f"Phase 1 complete: {posts_found} items from "
        f"{', '.join(f'{k}:{len(v)}' for k, v in raw_data.items())}"
    )
    if posts_found == 0:
        logging.error("All free sources returned 0 items. Check network connectivity.")
        sys.exit(2)

    # Phase 2: Analyze patterns
    analyzer       = ContentAnalyzer()
    pattern_report = analyzer.build_pattern_report(raw_data)
    logging.info(
        f"Analysis: {pattern_report['posts_analyzed']} posts | "
        f"{len(pattern_report['top_cta_patterns'])} CTA patterns"
    )

    # Phase 3: Generate topics
    client = OpenAI(api_key=config["openai"]["api_key"])
    topics = TopicGenerator(config, client).generate_topics(pattern_report)
    logging.info(f"Generated {len(topics)} topic ideas")

    # Phase 4: Enrich each topic with free web research
    rss_cache = raw_data.get("rss", [])
    enricher  = FreeWebEnricher(config, rss_cache=rss_cache)
    for topic in topics:
        logging.info(f"  Enriching: {topic['topic']}")
        web_data = enricher.enrich(topic["topic"])
        topic.update(web_data)

    # Phase 5: Write to Google Sheets
    rows_written = GoogleSheetsWriter(config).append_topics(topics)

    # Save audit JSON
    output_path = os.path.join(config["output_dir"], "logs", "research_output.json")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump({
            "run_timestamp":    datetime.utcnow().isoformat(),
            "pattern_report":   pattern_report,
            "topics_generated": topics,
            "rows_written":     rows_written,
        }, f, indent=2, ensure_ascii=False)
    logging.info(f"Audit saved: {output_path}")

    print(f"SUCCESS: {rows_written} topics written to Google Sheet")
    sys.exit(0)


if __name__ == "__main__":
    main()
