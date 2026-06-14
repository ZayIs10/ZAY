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

    recipient = os.getenv("NOTIFY_TO", sender)

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
        subject = f"[GenZ reel STAGED on IG — tap Publish] {topic}"
    else:
        subject = f"[GenZ reel ready to review] {topic}"

    caption_block = caption.strip() or "(no caption in sheet)"

    if staged_ok:
        ig_block = (
            "INSTAGRAM: STAGED & READY — JUST TAP PUBLISH\n"
            "------------------------------------------------------------\n"
            "The reel (with the caption below already attached) is uploaded to\n"
            "your Instagram and processed. To post it:\n"
            "  1. Open the Instagram app.\n"
            "  2. It's staged via the API — open your drafts/scheduled area or\n"
            "     the create flow; the video + caption are already prepared.\n"
            "  3. Review and tap Publish.\n"
            "Nothing goes live until you tap Publish.\n\n"
            "(If you don't see it staged in the app, just post manually from\n"
            "the Drive link above — the caption below is copy-paste ready.)\n"
        )
    else:
        reason = f" ({stage_detail})" if stage_detail else ""
        ig_block = (
            "INSTAGRAM: NOT STAGED — POST MANUALLY FROM DRIVE\n"
            "------------------------------------------------------------\n"
            f"Auto-staging to Instagram didn't run{reason}.\n"
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
