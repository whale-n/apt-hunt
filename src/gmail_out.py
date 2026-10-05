"""Outgoing Gmail: outreach DRAFTS (never sent) and notification emails to the user only."""

import base64
import logging
from email.message import EmailMessage

from src.config import cfg
from src.google_auth import gmail

log = logging.getLogger(__name__)


class RecipientNotAllowed(PermissionError):
    pass


def check_notification_recipient(to: str) -> str:
    allowed = cfg()["notify"]["to"].strip().lower()
    if not allowed:
        raise RecipientNotAllowed("NOTIFY_TO is not configured")
    if to.strip().lower() != allowed:
        raise RecipientNotAllowed(f"Notifications may only be sent to {allowed}, not {to!r}")
    return allowed


def _encode(msg: EmailMessage) -> str:
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


def create_draft(subject: str, body: str, to: str = "") -> str:
    """Create an outreach draft for the user to review and send themselves. Returns the Gmail draft id."""
    msg = EmailMessage()
    if to:
        msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    draft = gmail().users().drafts().create(userId="me", body={"message": {"raw": _encode(msg)}}).execute()
    return draft["id"]


def send_notification(subject: str, html: str, text: str = "", to: str | None = None) -> str:
    """Email the user (and only the user) about a listing."""
    recipient = check_notification_recipient(to or cfg()["notify"]["to"])
    msg = EmailMessage()
    msg["To"] = recipient
    msg["From"] = recipient
    msg["Subject"] = subject
    msg.set_content(text or "Open in an HTML-capable mail client.")
    msg.add_alternative(html, subtype="html")
    sent = gmail().users().messages().send(userId="me", body={"raw": _encode(msg)}).execute()
    from src.ingest.gmail import label_id

    gmail().users().messages().modify(
        userId="me",
        id=sent["id"],
        body={"addLabelIds": [label_id(cfg()["gmail"]["hot_label"]), "STARRED", "IMPORTANT", "INBOX", "UNREAD"]},
    ).execute()
    return sent["id"]
