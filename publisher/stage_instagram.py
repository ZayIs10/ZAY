"""Stage a finished reel on Instagram as a ready-to-publish media container.

Instagram's API has no true Drafts endpoint for reels. The container IS the
closest thing: video + caption are uploaded and processed server-side, leaving
a container that sits FINISHED until something calls media_publish on it.

This module's default `stage_reel()` does exactly that and STOPS: it creates
the container and waits for FINISHED, but does NOT schedule or publish. The
review email then carries the container id, and the user publishes it with one
click via the publish_reel_container GitHub workflow (which re-checks status
first). Containers expire ~24 h after creation, so review/publish same-day.

`schedule_reel_24h()` is kept for the older "auto-publish in 24 h via Meta
Planner" behaviour, but the reel build no longer calls it by default.

Both are intentionally best-effort: any failure here must NEVER undo an
already-rendered, already-on-Drive reel. The caller treats a False/None result
as "couldn't stage -- tell the user to post from Drive instead."
"""

from __future__ import annotations

import logging
import os
import time as _time

# How far ahead to schedule (seconds). 24 h gives plenty of review time.
_SCHEDULE_AHEAD_S = 24 * 60 * 60

log = logging.getLogger("stage_instagram")


class StageResult:
    """Outcome of a scheduling attempt."""

    def __init__(self, ok: bool, container_id: str = "", detail: str = ""):
        self.ok = ok
        self.container_id = container_id
        self.detail = detail


def stage_reel(video_url: str, caption: str) -> StageResult:
    """Create a ready-to-publish IG container (NO schedule, NO publish).

    Uploads the video + caption and waits for Instagram to finish processing,
    leaving a FINISHED container. The user publishes it later with one click
    (the publish_reel_container workflow), which re-checks status first.

    Returns a StageResult whose container_id is the creation id to publish.
    Never raises: on any problem it logs and returns ok=False with a
    human-readable detail, so the build email can tell the user to post
    manually from Drive instead.
    """
    access_token = os.getenv("INSTAGRAM_ACCESS_TOKEN")
    ig_user_id = os.getenv("INSTAGRAM_IG_USER_ID")
    if not access_token or not ig_user_id:
        msg = ("INSTAGRAM_ACCESS_TOKEN / INSTAGRAM_IG_USER_ID not set -- "
               "skipped IG staging.")
        log.warning(msg)
        return StageResult(False, detail=msg)
    if not video_url:
        return StageResult(False, detail="no video_url to stage")

    try:
        from publisher.publish_reel import (  # noqa: PLC0415
            create_reel_container, wait_for_container,
        )
    except Exception as exc:  # noqa: BLE001
        log.error("Could not import publish_reel helpers: %s", exc)
        return StageResult(False, detail=f"import error: {exc}")

    try:
        container_id = create_reel_container(
            ig_user_id, access_token, video_url, caption,
        )
        # Block until FINISHED so the click-to-publish link works immediately;
        # if processing errors out, wait_for_container raises SystemExit.
        wait_for_container(container_id, access_token)
    except SystemExit as exc:
        log.error("IG container creation failed: %s", exc)
        return StageResult(False, detail=f"container failed: {exc}")
    except Exception as exc:  # noqa: BLE001
        log.error("IG container error: %s", exc)
        return StageResult(False, detail=f"container error: {exc}")

    log.info("IG container ready to publish (container_id=%s).", container_id)
    return StageResult(
        True,
        container_id=container_id,
        detail="container ready -- publish with one click from the email",
    )


def schedule_reel_24h(video_url: str, caption: str) -> StageResult:
    """LEGACY: upload + schedule an IG reel 24 hours from now (Meta Planner).

    Kept for reference / manual use. The reel build no longer calls this — it
    uses stage_reel() (create-only) so the user controls publish timing.
    """
    access_token = os.getenv("INSTAGRAM_ACCESS_TOKEN")
    ig_user_id = os.getenv("INSTAGRAM_IG_USER_ID")
    if not access_token or not ig_user_id:
        msg = ("INSTAGRAM_ACCESS_TOKEN / INSTAGRAM_IG_USER_ID not set -- "
               "skipped IG scheduling.")
        log.warning(msg)
        return StageResult(False, detail=msg)
    if not video_url:
        return StageResult(False, detail="no video_url to schedule")

    try:
        from publisher.publish_reel import schedule_reel  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        log.error("Could not import schedule_reel: %s", exc)
        return StageResult(False, detail=f"import error: {exc}")

    scheduled_ts = int(_time.time()) + _SCHEDULE_AHEAD_S
    log.info("Scheduling reel for Unix ts %d (+24 h from now)", scheduled_ts)

    try:
        media_id = schedule_reel(
            ig_user_id, access_token, video_url, caption, scheduled_ts,
        )
    except SystemExit as exc:
        log.error("IG schedule call failed: %s", exc)
        return StageResult(False, detail=f"schedule failed: {exc}")
    except Exception as exc:  # noqa: BLE001
        log.error("IG schedule error: %s", exc)
        return StageResult(False, detail=f"schedule error: {exc}")

    log.info("IG reel scheduled (media_id=%s, ts=%d).", media_id, scheduled_ts)
    return StageResult(
        True,
        container_id=media_id,
        detail="scheduled for +24 h -- visible in Meta Business Suite Planner",
    )
