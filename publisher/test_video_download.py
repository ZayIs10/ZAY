"""Regression tests for the cookieless YouTube download + backup-recovery.

The bug these lock down: rows kept getting "Skipped — no video found" because
YouTube bot-blocked the cloud ("Sign in to confirm you're not a bot") — the
login-cookie secret had rotted. Fix = try multiple cookieless player clients
(tv/ios/android/web_safari) per URL, and try the next on-topic backup clip if
the primary won't download (no Pexels, no silent skip — alert if ALL fail).

These tests are pure logic (no network): candidate ordering/dedup, the
bot-block-vs-dead-video classifier, and the client attempt plan. The actual
cookieless download was verified live against two CI-failed videos
(vUdNaAAc4FY, Y9Wz2PV404E) — both downloaded via the android client with no
cookies.

Run:  python publisher/test_video_download.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from publisher import media_consumer as mc  # noqa: E402
from publisher import tweet_card_reel as tc  # noqa: E402


def test_candidates_primary_then_backups_deduped():
    row = {
        "Media Video URL": "https://youtube.com/watch?v=AAAAAAAAAAA",
        "Media Backups (JSON)": json.dumps({"video": [
            {"media_url": "https://youtube.com/watch?v=BBBBBBBBBBB"},
            {"page_url": "https://youtube.com/watch?v=CCCCCCCCCCC"},
            {"media_url": "https://youtube.com/watch?v=AAAAAAAAAAA"},  # dup
        ]}),
    }
    assert tc._candidate_video_urls(row) == [
        "https://youtube.com/watch?v=AAAAAAAAAAA",
        "https://youtube.com/watch?v=BBBBBBBBBBB",
        "https://youtube.com/watch?v=CCCCCCCCCCC",
    ]


def test_candidates_malformed_backups_degrade():
    row = {"Media Video URL": "x", "Media Backups (JSON)": "not json"}
    assert tc._candidate_video_urls(row) == ["x"]


def test_candidates_empty_row():
    assert tc._candidate_video_urls({"Media Video URL": ""}) == []


def test_bot_block_classifier_true_cases():
    for m in (
        "Sign in to confirm you're not a bot",
        "ERROR: [youtube] X: Requested format is not available",
        "Unable to extract player response",
    ):
        assert mc._is_bot_block(Exception(m)), m


def test_bot_block_classifier_false_cases():
    # A genuinely dead/private video is NOT a bot-block — another client
    # won't fix it, so we must not classify it as retryable.
    for m in (
        "Video unavailable: This video is private",
        "This video has been removed by the uploader",
        "HTTP Error 404: Not Found",
    ):
        assert not mc._is_bot_block(Exception(m)), m


def test_cookieless_clients_are_tried_first_and_web_is_not():
    # The cookieless attempt list must NOT include the bot-gated `web` client.
    assert "web" not in mc._YT_CLIENTS_COOKIELESS
    # tv leads — it's the most lenient cookieless client.
    assert mc._YT_CLIENTS_COOKIELESS[0] == "tv"
    assert "android" in mc._YT_CLIENTS_COOKIELESS


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL  {fn.__name__}: {exc}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
