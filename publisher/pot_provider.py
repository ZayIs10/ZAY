"""Keep the bgutil PO-token provider alive while yt-dlp downloads from YouTube.

Why this exists (2026-09-11, Denzel motivation row "Skipped - No Video"):
YouTube now demands a *PO token* (proof-of-origin) for real video formats
from every player client we use. Without one, from the home IP with a
3-month-old yt-dlp:
  - `tv`  -> "The page needs to be reloaded" (challenge page, no formats)
  - `ios` / `web` / `web_safari` -> "Requested format is not available"
  - `android` / `mweb` / `web_embedded` -> a 217 KB STUB whose header claims
    the full 9-minute duration but holds ~no frames (ffmpeg: "Cannot
    determine format of input after EOF") — so the reel builder gave up and
    burned the row as "no video", even though the sheet's hand-picked URL was
    perfectly fine and had been read correctly.
  - DASH formats (398+251) -> HTTP 403 on the actual media URL.
A verbose run showed the smoking gun: `[pot] PO Token Providers: none`.

Fix: the `bgutil-ytdlp-pot-provider` yt-dlp plugin (pip) + its Node server
(cloned to BGUTIL_POT_HOME, built with `npm ci && npx tsc`). yt-dlp's plugin
auto-discovers the server at http://127.0.0.1:4416 and attaches a token to
every request; verified 2026-09-11 that `mweb` and `web_embedded` then serve
the real 34 MB 720p file where before they served the stub.

We run the provider in HTTP-SERVER mode, not "script" mode, because the
script (generate_once.js) needs ~12 s just to load its dependencies on the
runner, which trips the plugin's 15 s `--version` health check and it marks
itself unavailable. The server pays that startup once per build instead.

`ensure_running()` is best-effort: it never raises. If the provider isn't
installed, we log loudly and continue (the download may still work for some
videos / clients). Set DISABLE_POT_PROVIDER=1 to skip it entirely.
"""
from __future__ import annotations

import atexit
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

import requests

log = logging.getLogger(__name__)

DEFAULT_PORT = 4416
_proc: subprocess.Popen | None = None
_checked_once = False


def _port() -> int:
    try:
        return int(os.environ.get("BGUTIL_POT_PORT", "").strip() or DEFAULT_PORT)
    except ValueError:
        return DEFAULT_PORT


def _server_home() -> Path | None:
    """Folder holding the provider's built server (`build/main.js`)."""
    candidates: list[Path] = []
    env = os.environ.get("BGUTIL_POT_HOME", "").strip()
    if env:
        candidates.append(Path(env))
    if os.name == "nt":
        # Self-hosted runner (marc-pc): outside the repo so checkout-clean
        # keeps it, next to the persistent venv.
        candidates.append(Path(r"C:\actions-runner\bgutil-pot\server"))
    # The plugin's own default location (what the workflow clones to on
    # ubuntu-latest and what a local `git clone` in $HOME produces).
    candidates.append(Path.home() / "bgutil-ytdlp-pot-provider" / "server")
    for c in candidates:
        if (c / "build" / "main.js").exists():
            return c
    return None


def _ping(port: int) -> bool:
    try:
        r = requests.get(f"http://127.0.0.1:{port}/ping", timeout=3)
        return r.status_code == 200
    except requests.RequestException:
        return False


def _stop() -> None:
    global _proc
    if _proc is not None and _proc.poll() is None:
        try:
            _proc.terminate()
            _proc.wait(timeout=10)
        except Exception:  # noqa: BLE001 — best-effort cleanup
            try:
                _proc.kill()
            except Exception:  # noqa: BLE001
                pass
    _proc = None


def ensure_running(timeout_s: float = 90.0) -> bool:
    """Make sure a PO-token server answers on 127.0.0.1:<port>.

    Returns True when a provider is reachable (already running, or started
    here), False when unavailable. Never raises.
    """
    global _proc, _checked_once
    if os.environ.get("DISABLE_POT_PROVIDER", "").strip() == "1":
        if not _checked_once:
            log.info("PO-token provider disabled via DISABLE_POT_PROVIDER=1.")
        _checked_once = True
        return False

    port = _port()
    if _ping(port):
        if not _checked_once:
            log.info("PO-token provider already running on 127.0.0.1:%d.", port)
        _checked_once = True
        return True
    if _proc is not None and _proc.poll() is None:
        # We started it but it isn't answering (yet) — give it a moment.
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if _ping(port):
                return True
            time.sleep(2)
        return False

    home = _server_home()
    if home is None:
        log.warning(
            "PO-token provider NOT installed (no build/main.js under "
            "BGUTIL_POT_HOME / C:\\actions-runner\\bgutil-pot\\server / "
            "~/bgutil-ytdlp-pot-provider/server). YouTube downloads may 403 "
            "or return stub files. Install: git clone --branch 2.0.0 "
            "https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git && "
            "cd server && npm ci && npx tsc")
        _checked_once = True
        return False
    node = shutil.which("node")
    if not node:
        log.warning("PO-token provider present at %s but `node` is not on "
                    "PATH — cannot start it.", home)
        _checked_once = True
        return False

    log.info("Starting PO-token provider: node build/main.js --port %d "
             "(cwd=%s)", port, home)
    try:
        _proc = subprocess.Popen(
            [node, "build/main.js", "--port", str(port)],
            cwd=str(home),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        log.warning("Could not start PO-token provider: %s", exc)
        _checked_once = True
        return False
    atexit.register(_stop)

    t0 = time.time()
    deadline = t0 + timeout_s
    while time.time() < deadline:
        if _proc.poll() is not None:
            log.warning("PO-token provider exited immediately (code %s).",
                        _proc.returncode)
            _proc = None
            _checked_once = True
            return False
        if _ping(port):
            log.info("PO-token provider ready after %.0fs.", time.time() - t0)
            _checked_once = True
            return True
        time.sleep(2)
    log.warning("PO-token provider did not answer within %.0fs — continuing "
                "without it.", timeout_s)
    _checked_once = True
    return False


if __name__ == "__main__":  # quick manual check: python -m publisher.pot_provider
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ok = ensure_running()
    print("provider reachable:", ok)
