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
    """Compose the (subject, body) for a 'reel ready to review' email.

    `caption` is the full Post Caption from the sheet — it already includes
    the hashtags, so it's sent verbatim as one copy-paste-ready block.

    `staged_ok` True means the reel is sitting on Instagram as a FINISHED
    container ready to publish. With `container_id` + `repo` set, the email
    carries two one-click GitHub links: PUBLISH NOW and CHECK STATUS. False
    means staging was skipped/failed and the user should post manually from
    the Drive link — `stage_detail` says why.
    """
    have_links = bool(staged_ok and container_id and repo)

    if have_links:
        subject = f"[GenZ reel READY — 1-click publish] {topic}"
    elif staged_ok:
        subject = f"[GenZ reel staged on IG] {topic}"
    else:
        subject = f"[GenZ reel ready to review] {topic}"

    caption_block = caption.strip() or "(no caption in sheet)"

    if have_links:
        publish_url = _dispatch_url(repo, container_id, "publish")
        check_url = _dispatch_url(repo, container_id, "check")
        ig_block = (
            "INSTAGRAM: READY TO PUBLISH (nothing is live yet)\n"
            "------------------------------------------------------------\n"
            "The reel is uploaded to Instagram as a finished container and\n"
            "is waiting for your go-ahead. NOTHING posts until you click.\n\n"
            "  >> PUBLISH NOW (posts the reel live):\n"
            f"     {publish_url}\n\n"
            "  >> CHECK STATUS FIRST (reports any processing error, posts\n"
            "     nothing):\n"
            f"     {check_url}\n\n"
            "  After the page opens, press the green \"Run workflow\" button.\n"
            "  (You're already signed in to GitHub, so your IG token stays\n"
            "  locked in GitHub Secrets — it is never in this email.)\n\n"
            f"  Container id: {container_id}\n"
            "  NOTE: the container expires ~24 h after render — publish today.\n"
        )
    elif staged_ok:
        # Staged but we couldn't build links (missing repo/container id).
        ig_block = (
            "INSTAGRAM: STAGED\n"
            "------------------------------------------------------------\n"
            "The reel is staged on Instagram"
            + (f" (container {container_id})" if container_id else "")
            + ".\nPublish it from the publish_reel_container GitHub workflow.\n"
        )
    else:
        reason = f" ({stage_detail})" if stage_detail else ""
        ig_block = (
            "INSTAGRAM: NOT STAGED — POST MANUALLY FROM DRIVE\n"
            "------------------------------------------------------------\n"
            f"Staging to Instagram didn't run{reason}.\n"
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
