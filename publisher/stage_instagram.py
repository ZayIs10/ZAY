"""Stage a finished reel onto Instagram WITHOUT publishing it.

The user's rule (chosen 2026-06-14): "Stage only, I tap Publish." Instagram's
Graph API has no real Drafts endpoint for reels — the closest is a media
CONTAINER: the video + caption are uploaded and processed server-side, then a
separate publish call makes it public. We do the first half (create + wait for
FINISHED) and STOP. The user taps Publish in the Instagram app.

This is intentionally best-effort: any failure here (expired token, processing
error, missing creds) must NEVER undo an already-rendered, already-on-Drive
reel. The caller treats a False/None result as "couldn't stage — tell the user
to post from the Drive link instead," not as a build failure.

IMPORTANT: this module must never call media_publish. Publishing is the user's
manual step. Reuses the container helpers in publish_reel.py.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger("stage_instagram")


class StageResult:
    """Outcome of a staging attempt. `ok` True only when a container reached
    FINISHED and is ready for the user to publish."""

    def __init__(self, ok: bool, container_id: str = "", detail: str = ""):
        self.ok = ok
        self.container_id = container_id
        self.detail = detail


def stage_reel(video_url: str, caption: str) -> StageResult:
    """Create + process (but do NOT publish) an IG reel container.

    `video_url` must be a public direct-download URL Instagram can fetch
    (the Drive URL the build already produced). `caption` is the Post Caption
    (with hashtags) — what shows in the IG text box once the user publishes.

    Returns a StageResult. Never raises: on any problem it logs and returns
    ok=False with a human-readable detail, so the build's email can tell the
    user to post manually from Drive.
    """
    access_token = os.getenv("INSTAGRAM_ACCESS_TOKEN")
    ig_user_id = os.getenv("INSTAGRAM_IG_USER_ID")
    if not access_token or not ig_user_id:
        msg = ("INSTAGRAM_ACCESS_TOKEN / INSTAGRAM_IG_USER_ID not set — "
               "skipped IG staging.")
        log.warning(msg)
        return StageResult(False, detail=msg)
    if not video_url:
        return StageResult(False, detail="no video_url to stage")

    # Late import: keeps `requests` off the path for dry runs / environments
    # that never stage. Reuses the proven container helpers.
    try:
        from publisher.publish_reel import (
            create_reel_container,
            wait_for_container,
        )
    except Exception as exc:  # noqa: BLE001
        log.error("Could not import IG container helpers: %s", exc)
        return StageResult(False, detail=f"import error: {exc}")

    try:
        container_id = create_reel_container(
            ig_user_id, access_token, video_url, caption,
        )
    except SystemExit as exc:
        # create_reel_container calls sys.exit() on a non-200 (e.g. bad token).
        # Catch it so a staging failure can't kill the build process.
        log.error("IG container creation failed: %s", exc)
        return StageResult(False, detail=f"container creation failed: {exc}")
    except Exception as exc:  # noqa: BLE001
        log.error("IG container creation error: %s", exc)
        return StageResult(False, detail=f"container error: {exc}")

    try:
        # Reels need server-side processing before they're publishable.
        wait_for_container(container_id, access_token, max_wait_s=600)
    except SystemExit as exc:
        log.error("IG container did not finish processing: %s", exc)
        return StageResult(
            False, container_id=container_id,
            detail=f"processing failed: {exc}",
        )
    except Exception as exc:  # noqa: BLE001
        log.error("IG container wait error: %s", exc)
        return StageResult(
            False, container_id=container_id,
            detail=f"processing error: {exc}",
        )

    log.info("IG reel staged (NOT published): container_id=%s", container_id)
    return StageResult(True, container_id=container_id,
                       detail="staged — tap Publish in Instagram")
