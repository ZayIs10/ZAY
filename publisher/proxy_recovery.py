"""Rebuild reels that were parked because the residential proxy ran out
of traffic (Status = "Proxy Empty - Retry").

Run by .github/workflows/proxy_recovery.yml every 6 hours (and manually
via workflow_dispatch). Flow:

  1. Probe the proxy with a ~1KB request. Dead (407 TRAFFIC_EXHAUSTED /
     tunnel failure) -> exit 0 quietly; nothing can build anyway.
  2. Alive -> find every Reels row parked at "Proxy Empty - Retry" and
     run the normal build for each (sequentially, so a fan-out can't
     blow the Sheets 60-reads/min quota).

Each build claims/finishes its own row exactly like an n8n-dispatched
run ("Building" -> "Ready to Post" / "Skipped - No Video" / re-parked),
so this script does no status bookkeeping of its own.

The probe deliberately builds a private opener — NEVER set
http(s)_proxy in os.environ (it breaks Google Sheets auth; see
system_map.md Known traps).
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

from publisher.post_generator import SheetsReader  # noqa: E402
from publisher.tweet_card_reel import (  # noqa: E402
    PROXY_EMPTY_STATUS, _sheets_config,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("proxy_recovery")

PROBE_URL = "https://www.google.com/generate_204"  # ~1KB round-trip
# Safety cap per run: 6 rebuilds x ~5-10 min render each stays well inside
# the workflow timeout. Leftovers are picked up by the next 6h cron tick.
MAX_BUILDS_PER_RUN = 6
# Breather between sequential builds — each build does a handful of Sheets
# reads/writes and the service-account quota is 60/min shared by everything.
PAUSE_BETWEEN_BUILDS_S = 15


def probe_proxy(proxy_url: str) -> tuple[bool, str]:
    """True if a tiny HTTPS request tunnels through the proxy."""
    handler = urllib.request.ProxyHandler(
        {"http": proxy_url, "https": proxy_url})
    opener = urllib.request.build_opener(handler)
    req = urllib.request.Request(
        PROBE_URL, headers={"User-Agent": "genz-proxy-probe"})
    try:
        with opener.open(req, timeout=25) as resp:
            return True, f"HTTP {resp.status}"
    except Exception as exc:  # noqa: BLE001 — any failure = proxy unusable
        return False, str(exc)


def _parked_topics(reader: SheetsReader) -> list[tuple[int, str]]:
    """(row_index, topic) for every row parked at PROXY_EMPTY_STATUS."""
    values = reader.ws.get_all_values()
    if not values:
        return []
    headers = values[0]
    want = PROXY_EMPTY_STATUS.strip().lower()
    out: list[tuple[int, str]] = []
    for i, raw in enumerate(values[1:], start=2):
        row = {headers[j]: raw[j] if j < len(raw) else ""
               for j in range(len(headers))}
        status = str(row.get("Status", "")).strip().lower()
        topic = str(row.get("Topic", "")).strip()
        if status == want and topic:
            out.append((i, topic))
    return out


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")
    proxy_url = os.getenv("PROXY_URL", "").strip()
    if not proxy_url:
        log.info("PROXY_URL not set — nothing to recover (direct-download "
                 "mode, e.g. self-hosted runner). Exiting.")
        return 0

    ok, detail = probe_proxy(proxy_url)
    if not ok:
        log.info("Proxy still unusable (%s) — parked rows stay parked; "
                 "next probe in ~6h. Top up DataImpulse to unblock.", detail)
        return 0
    log.info("Proxy is ALIVE (%s) — checking for parked rows...", detail)

    reader = SheetsReader(_sheets_config())
    parked = _parked_topics(reader)
    if not parked:
        log.info("No rows at Status=%r. Nothing to do.", PROXY_EMPTY_STATUS)
        return 0

    todo = parked[:MAX_BUILDS_PER_RUN]
    if len(parked) > len(todo):
        log.warning("%d parked rows found; building %d this run — the "
                    "remaining %d will go on the next 6h tick.",
                    len(parked), len(todo), len(parked) - len(todo))

    failures = 0
    for n, (row_index, topic) in enumerate(todo, start=1):
        log.info("[%d/%d] Rebuilding row %d: %r", n, len(todo),
                 row_index, topic)
        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "publisher" / "tweet_card_reel.py"),
             "--topic", topic],
            cwd=REPO_ROOT,
        )
        if proc.returncode != 0:
            # The build already wrote its own failure status + email; just
            # surface it in this run's log/exit code.
            log.error("[%d/%d] Build exited %d for %r.", n, len(todo),
                      proc.returncode, topic)
            failures += 1
        if n < len(todo):
            time.sleep(PAUSE_BETWEEN_BUILDS_S)

    log.info("Recovery pass done: %d attempted, %d hard failures.",
             len(todo), failures)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
