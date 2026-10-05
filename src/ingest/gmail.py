"""Read listing-alert emails (StreetEasy, Zillow, Facebook groups, ...) from the Gmail feed label."""

import base64
import logging
from dataclasses import dataclass
from email.utils import parseaddr

from src.config import cfg
from src.google_auth import gmail

log = logging.getLogger(__name__)


@dataclass
class RawEmail:
    id: str
    sender: str
    subject: str
    date: str
    html: str
    text: str

    @property
    def source(self) -> str:
        domain = parseaddr(self.sender)[1].lower()
        for source, needles in cfg()["gmail"]["feed_senders"].items():
            if any(n in domain for n in needles):
                return source
        return "other"


_label_ids: dict[str, str] = {}


def label_id(name: str, create: bool = True) -> str:
    if not _label_ids:
        for lab in gmail().users().labels().list(userId="me").execute().get("labels", []):
            _label_ids[lab["name"]] = lab["id"]
    if name not in _label_ids and create:
        lab = gmail().users().labels().create(
            userId="me", body={"name": name, "labelListVisibility": "labelShow", "messageListVisibility": "show"}
        ).execute()
        _label_ids[name] = lab["id"]
    return _label_ids[name]


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data.encode()).decode("utf-8", errors="replace")


def _walk(payload: dict, out: dict) -> None:
    mime = payload.get("mimeType", "")
    body = payload.get("body", {}).get("data")
    if body and mime == "text/html":
        out["html"] += _decode(body)
    elif body and mime == "text/plain":
        out["text"] += _decode(body)
    for part in payload.get("parts", []) or []:
        _walk(part, out)


def fetch_unprocessed(days: int = 3, max_results: int = 50) -> list[RawEmail]:
    g = cfg()["gmail"]
    q = f"label:{g['feed_label']} -label:{g['processed_label']} newer_than:{days}d"
    resp = gmail().users().messages().list(userId="me", q=q, maxResults=max_results).execute()
    emails = []
    for ref in resp.get("messages", []):
        msg = gmail().users().messages().get(userId="me", id=ref["id"], format="full").execute()
        headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}
        parts = {"html": "", "text": ""}
        _walk(msg["payload"], parts)
        emails.append(
            RawEmail(
                id=msg["id"],
                sender=headers.get("from", ""),
                subject=headers.get("subject", ""),
                date=headers.get("date", ""),
                html=parts["html"],
                text=parts["text"],
            )
        )
    return emails


def mark_processed(message_id: str) -> None:
    gmail().users().messages().modify(
        userId="me", id=message_id, body={"addLabelIds": [label_id(cfg()["gmail"]["processed_label"])]}
    ).execute()
