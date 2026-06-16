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


def build_review_email(
    *, topic: str, caption: str, drive_url: str,
    staged_ok: bool = False, stage_detail: str = "",
) -> tuple[str, str]:
    """Compose the (subject, body) for a 'reel ready to review' email.

    `caption` is the full Post Caption from the sheet — it already includes
    the hashtags, so it's sent verbatim as one copy-paste-ready block.

    `staged_ok` True means the reel was uploaded to Instagram as a staged
    container (caption already attached) and the user just has to tap Publish
    in the app. False means staging was skipped/failed and the user should
    post manually from the Drive link — `stage_detail` says why.
    """
    if staged_ok:
        subject = f"[GenZ reel SCHEDULED on IG — review in Meta Planner] {topic}"
    else:
        subject = f"[GenZ reel ready to review] {topic}"

    caption_block = caption.strip() or "(no caption in sheet)"

    if staged_ok:
        ig_block = (
            "INSTAGRAM: SCHEDULED FOR +24 HOURS\n"
            "------------------------------------------------------------\n"
            "The reel is scheduled to auto-publish in 24 hours.\n"
            "To review it before it goes live:\n"
            "  1. Go to business.facebook.com/latest/content_scheduler\n"
            "  2. Find the scheduled post in the Planner calendar.\n"
            "  3. Tap it to preview the video + caption.\n"
            "  4. If it looks good — do nothing, it posts automatically.\n"
            "  5. If you want to cancel — tap the post and click Delete.\n\n"
            "(If you don't see it in the Planner, use the Drive link below\n"
            "to post manually — the caption is copy-paste ready.)\n"
        )
    else:
        reason = f" ({stage_detail})" if stage_detail else ""
        ig_block = (
            "INSTAGRAM: NOT SCHEDULED — POST MANUALLY FROM DRIVE\n"
            "------------------------------------------------------------\n"
            f"Auto-scheduling to Instagram didn't run{reason}.\n"
            "Download the reel from the Drive link above and post it yourself;\n"
            "the caption below is copy-paste ready (includes hashtags).\n"
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
