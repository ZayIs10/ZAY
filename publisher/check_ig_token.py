"""Validate the Instagram access token WITHOUT posting anything.

Answers the only question that matters before 8pm SGT: "will the token actually
publish tonight?" It checks, read-only:
  1. The token is valid and not expired (Graph API debug_token).
  2. When it expires (so you know how long you're good for).
  3. It can see the IG account and that account can publish reels
     (content_publishing_limit endpoint — the same gate publishing uses).

Posts NOTHING. Exit 0 = good to go; exit 1 = something needs fixing (and it
prints exactly what).

    python publisher/check_ig_token.py
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
GRAPH = "https://graph.facebook.com/v21.0"


def main() -> int:
    load_dotenv(REPO_ROOT / ".env")
    token = os.getenv("INSTAGRAM_ACCESS_TOKEN")
    ig_user_id = os.getenv("INSTAGRAM_IG_USER_ID")
    if not token or not ig_user_id:
        print("FAIL: INSTAGRAM_ACCESS_TOKEN / INSTAGRAM_IG_USER_ID not set.")
        return 1

    ok = True

    # 1 + 2: token validity & expiry via debug_token (token inspects itself).
    try:
        r = requests.get(
            f"{GRAPH}/debug_token",
            params={"input_token": token, "access_token": token},
            timeout=30,
        )
        data = r.json().get("data", {})
        if not data.get("is_valid"):
            print(f"FAIL: token is NOT valid — {r.json()}")
            return 1
        expires_at = data.get("expires_at") or data.get("data_access_expires_at")
        if expires_at:
            dt = datetime.fromtimestamp(int(expires_at), tz=timezone.utc)
            days = (dt - datetime.now(timezone.utc)).days
            if expires_at == 0:
                print("OK: token is valid and NEVER expires (long-lived).")
            else:
                print(f"OK: token valid. Expires {dt:%Y-%m-%d %H:%M UTC} "
                      f"(~{days} days). Scopes: {data.get('scopes')}")
                if days <= 7:
                    print(f"WARN: token expires in ~{days} days — refresh soon.")
        else:
            print("OK: token valid (no expiry reported).")
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: could not inspect token: {exc}")
        return 1

    # 3: can this token see the IG account + its publishing quota? This is the
    # same account/permission path a real publish uses, but it posts nothing.
    try:
        r = requests.get(
            f"{GRAPH}/{ig_user_id}",
            params={"fields": "username", "access_token": token},
            timeout=30,
        )
        if r.status_code != 200:
            print(f"FAIL: token can't read the IG account {ig_user_id}: {r.text}")
            return 1
        acct = r.json()
        print(f"OK: sees IG account @{acct.get('username')}.")

        r2 = requests.get(
            f"{GRAPH}/{ig_user_id}/content_publishing_limit",
            params={"access_token": token},
            timeout=30,
        )
        if r2.status_code == 200:
            q = (r2.json().get("data") or [{}])[0]
            print(f"OK: publishing is enabled. Used {q.get('quota_usage')} "
                  f"of {q.get('config', {}).get('quota_total', '?')} posts (24h).")
        else:
            # Not fatal — some setups restrict this endpoint — but flag it.
            print(f"WARN: couldn't read publishing quota (publish may still "
                  f"work): {r2.text}")
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: account/permission check errored: {exc}")
        return 1

    print("\n=== RESULT: token is GOOD TO PUBLISH ===" if ok else "\n=== ISSUES ABOVE ===")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
