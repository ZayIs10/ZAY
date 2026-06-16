"""Schedule a finished reel on Instagram 24 hours ahead so it appears in
Meta Business Suite Planner for review before going live automatically.

Instagram's API has no true Drafts endpoint for reels. The closest equivalent
is a SCHEDULED post: video + caption are uploaded, processed, then published
with a future `scheduled_publish_time`. The post shows up in Meta Business
Suite -> Planner so the user can tap it, preview it, and cancel or let it
auto-publish at the scheduled time.

Default schedule: now + 24 hours (enough time to review; can be cancelled in
Meta Business Suite before the time fires).

This is intentionally best-effort: any failure here must NEVER undo an
already-rendered, already-on-Drive reel. The caller treats a False/None result
as "couldn't schedule -- tell the user to post from Drive instead."
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
    """Upload + schedule an IG reel 24 hours from now.

    The post appears immediately in Meta Business Suite -> Planner so the user
    can review it. It auto-publishes at the scheduled time unless cancelled.

    Returns a StageResult. Never raises: on any problem it logs and returns
    ok=False with a human-readable detail, so the build email can tell the
    user to post manually from Drive instead.
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
        detail=f"scheduled for +24 h -- visible in Meta Business Suite Planner",
    )
