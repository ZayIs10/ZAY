"""Send a plain-text review email from the CI render worker.

Used by tweet_card_reel.py after a reel renders + uploads to Drive: it
emails the user the Drive link plus the Instagram caption + hashtags
(pulled from the Sheet row) so they can review before posting.

Transport is Gmail SMTP with an app password, because the GitHub Actions
runner has no browser to do an interactive Google sign-in. Required env:

  GMAIL_ADDRESS       the sending Gmail account (also the default recipient)
  GMAIL_APP_PASSWORD  a 16-char Google "app password" (NOT the login password)
  NOTIFY_TO           optional; recipient override (defaults to GMAIL_ADDRESS)

If GMAIL_ADDRESS / GMAIL_APP_PASSWORD are unset, send() logs a warning and
returns False rather than raising — a missing notifier must never fail an
otherwise-successful render.
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage
from urllib.parse import quote

log = logging.getLogger("notify_email")

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465  # implicit TLS


def send(subject: str, body: str) -> bool:
    """Send a plain-text email. True on success, False if skipped/failed."""
    sender = os.getenv("GMAIL_ADDRESS")
    password = os.getenv("GMAIL_APP_PASSWORD")
    if not sender or not password:
        log.warning(
            "GMAIL_ADDRESS / GMAIL_APP_PASSWORD unset — skipping review email."
        )
        return False

    # NOTIFY_TO may be PRESENT-but-EMPTY (the GitHub workflow always sets the
    # env var, even when the secret is unset -> ""). os.getenv's default only
    # kicks in when the var is missing, not when it's "", so an empty value
    # would make the recipient blank and Gmail rejects it with
    # "555 5.5.2 Syntax error, cannot decode response". Fall back to the sender
    # whenever NOTIFY_TO is empty/whitespace.
    recipient = (os.getenv("NOTIFY_TO") or "").strip() or sender

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.set_content(body)

    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            server.login(sender, password)
            server.send_message(msg)
        log.info("Review email sent to %s", recipient)
        return True
    except Exception as exc:  # noqa: BLE001 — email failure must not fail render
        log.error("Failed to send review email: %s", exc)
        return False


# GitHub workflow that publishes (or checks) a single IG container by id.
_PUBLISH_WORKFLOW = "publish_reel_container.yml"


def _dispatch_url(repo: str, container_id: str, mode: str) -> str:
    """Build a one-click GitHub Actions link that pre-fills the publish form.

    GitHub's Actions UI reads `?container_id=...&mode=...` query params into the
    matching workflow_dispatch inputs, so the link lands the user on the "Run
    workflow" panel with everything filled — they just press the green button.
    The IG token never leaves GitHub Secrets; nothing sensitive is in the email.
    """
    base = f"https://github.com/{repo}/actions/workflows/{_PUBLISH_WORKFLOW}"
    return f"{base}?container_id={quote(container_id)}&mode={mode}"


def build_review_email(
    *, topic: str, caption: str, drive_url: str,
    staged_ok: bool = False, stage_detail: str = "",
    container_id: str = "", repo: str = "",
) -> tuple[str, str]:
    """Compose the (subject, body) for a 'reel rendered + queued' email.

    `caption` is the full Post Caption from the sheet — it already includes
    the hashtags, so it's sent verbatim as one copy-paste-ready block.

    The reel build no longer publishes to Instagram itself (the IG API can't
    truly schedule — sending scheduled_publish_time is silently ignored and the
    post goes out instantly). Instead the row is left at Status="Ready to Post"
    and a daily GitHub cron publishes it at 8pm SGT — the peak SG/MY window.
    So this email tells the user the reel is QUEUED for 8pm SGT, with the Drive
    link to post sooner if they want. (`staged_ok` / `stage_detail` /
    `container_id` / `repo` are kept for signature compatibility but unused.)
    """
    subject = f"[GenZ reel queued — auto-posts 8pm SGT] {topic}"

    caption_block = caption.strip() or "(no caption in sheet)"

    ig_block = (
        "INSTAGRAM: QUEUED — AUTO-PUBLISHES AT 8PM SGT\n"
        "------------------------------------------------------------\n"
        "This reel is queued (Status = 'Ready to Post'). A scheduled job\n"
        "publishes it automatically at the next 8:00 PM SGT — the peak\n"
        "Singapore/Malaysia evening window — with the caption below.\n\n"
        "  * Want it out sooner? Download from the Drive link above and\n"
        "    post it manually now.\n"
        "  * Don't want it posted? Open the sheet and change this row's\n"
        "    Status away from 'Ready to Post' before 8pm SGT.\n"
    )

    body = (
        f"Your reel for \"{topic}\" is rendered and uploaded.\n\n"
        f"WATCH / DOWNLOAD:\n{drive_url}\n\n"
        f"------------------------------------------------------------\n"
        f"{ig_block}"
        f"\n------------------------------------------------------------\n"
        f"INSTAGRAM CAPTION (copy-paste ready — includes hashtags)\n"
        f"------------------------------------------------------------\n"
        f"{caption_block}\n"
    )
    return subject, body
