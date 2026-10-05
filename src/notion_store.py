"""Thin Notion REST client shared by the poller and the local UI. Notion is the source of truth."""

import logging
import time
from datetime import datetime, timezone

import httpx

from src.config import cfg, env
from src.models import Listing

log = logging.getLogger(__name__)

API = "https://api.notion.com/v1"
TEXT_LIMIT = 2000  # Notion caps each rich_text object at 2000 chars


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _rich(text: str | None) -> dict:
    text = text or ""
    chunks = [text[i : i + TEXT_LIMIT] for i in range(0, min(len(text), TEXT_LIMIT * 10), TEXT_LIMIT)]
    return {"rich_text": [{"type": "text", "text": {"content": c}} for c in chunks]}


def _title(text: str) -> dict:
    return {"title": [{"type": "text", "text": {"content": (text or "Untitled")[:TEXT_LIMIT]}}]}


def _select(name: str | None) -> dict:
    return {"select": {"name": name} if name else None}


def _date(value: str | None) -> dict:
    return {"date": {"start": value} if value else None}


def _number(value) -> dict:
    return {"number": value}


def _plain(prop: dict) -> object:
    """Flatten a Notion property value into a plain Python value."""
    t = prop["type"]
    v = prop.get(t)
    if t in ("title", "rich_text"):
        return "".join(part.get("plain_text", "") for part in v or [])
    if t == "select":
        return v["name"] if v else None
    if t == "multi_select":
        return [o["name"] for o in v or []]
    if t == "date":
        return v["start"] if v else None
    if t in ("number", "url", "email", "phone_number", "checkbox"):
        return v
    if t in ("created_time", "last_edited_time"):
        return v
    return None


class NotionStore:
    def __init__(self, token: str | None = None):
        self.http = httpx.Client(
            base_url=API,
            timeout=30,
            headers={
                "Authorization": f"Bearer {token or env('NOTION_TOKEN')}",
                "Notion-Version": "2022-06-28",
                "Content-Type": "application/json",
            },
        )
        n = cfg()["notion"]
        self.listings_db = n["listings_db"]
        self.buildings_db = n["buildings_db"]

    def _req(self, method: str, path: str, **kw) -> dict:
        for attempt in range(5):
            r = self.http.request(method, path, **kw)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(float(r.headers.get("retry-after", 2 ** attempt)))
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"Notion {method} {path} -> {r.status_code}: {r.text[:500]}")
            return r.json()
        r.raise_for_status()
        return r.json()

    # ---- generic ----
    def query(self, db: str, filter: dict | None = None, sorts: list | None = None, limit: int | None = None):
        body: dict = {"page_size": 100}
        if filter:
            body["filter"] = filter
        if sorts:
            body["sorts"] = sorts
        seen = 0
        while True:
            data = self._req("POST", f"/databases/{db}/query", json=body)
            for page in data["results"]:
                yield page
                seen += 1
                if limit and seen >= limit:
                    return
            if not data.get("has_more"):
                return
            body["start_cursor"] = data["next_cursor"]

    def update(self, page_id: str, properties: dict) -> dict:
        return self._req("PATCH", f"/pages/{page_id}", json={"properties": properties})

    def get(self, page_id: str) -> dict:
        return self._req("GET", f"/pages/{page_id}")

    @staticmethod
    def flatten(page: dict) -> dict:
        out = {name: _plain(p) for name, p in page["properties"].items()}
        out["id"] = page["id"]
        out["notion_url"] = page.get("url")
        out["created_time"] = page.get("created_time")
        out["last_edited_time"] = page.get("last_edited_time")
        return out

    # ---- listings ----
    def find_listing(self, dedupe_key: str, cross_key: str | None = None) -> dict | None:
        ors = [{"property": "Dedupe key", "rich_text": {"contains": dedupe_key}}]
        if cross_key:
            ors.append({"property": "Dedupe key", "rich_text": {"contains": cross_key}})
        for page in self.query(self.listings_db, filter={"or": ors}, limit=1):
            return page
        return None

    def create_listing(self, listing: Listing, score: dict) -> dict:
        keys = " ".join(k for k in (listing.dedupe_key, listing.cross_source_key) if k)
        props = {
            "Title": _title(listing.title or listing.address or listing.url),
            "URL": {"url": listing.url or None},
            "Source": _select(listing.source),
            "Address": _rich(listing.address),
            "Unit": _rich(listing.unit),
            "Price": _number(listing.price),
            "Net effective": _number(score.get("net_effective")),
            "Concession": _rich(score.get("concession")),
            "Beds": _select(listing.beds if listing.beds in ("studio", "1br", "2br") else None),
            "Sqft": _number(listing.sqft or score.get("est_sqft")),
            "Walk min": _number(listing.walk_min),
            "Lat": _number(listing.lat),
            "Lng": _number(listing.lng),
            "Score": _number(score.get("score")),
            "Category": _select(score.get("category")),
            "Tags": {"multi_select": [{"name": t} for t in score.get("tags", [])]},
            "Score reasons": _rich(score.get("reasons")),
            "Photos": _rich("\n".join(listing.photos[:12])),
            "Description": _rich(listing.description),
            "Available": _date(listing.available),
            "Posted at": _date(listing.posted_at or now_iso()),
            "Status": _select("New"),
            "Draft": _rich(score.get("draft")),
            "Dedupe key": _rich(keys),
            "Contact": _rich(listing.contact),
        }
        return self._req("POST", "/pages", json={"parent": {"database_id": self.listings_db}, "properties": props})

    def set_listing_fields(self, page_id: str, **fields) -> dict:
        """Update listing fields by friendly name: status, vote, draft, gmail_draft_id, notes, viewing_at, notified_at."""
        mapping = {
            "status": ("Status", _select),
            "vote": ("My vote", _select),
            "draft": ("Draft", _rich),
            "gmail_draft_id": ("Gmail draft ID", _rich),
            "notes": ("Notes", _rich),
            "viewing_at": ("Viewing at", _date),
            "notified_at": ("Notified at", _date),
        }
        props = {}
        for key, value in fields.items():
            name, fn = mapping[key]
            props[name] = fn(value)
        return self.update(page_id, props)

    def voted_examples(self, limit: int = 12) -> list[dict]:
        pages = self.query(
            self.listings_db,
            filter={"property": "My vote", "select": {"is_not_empty": True}},
            sorts=[{"timestamp": "last_edited_time", "direction": "descending"}],
            limit=limit,
        )
        return [self.flatten(p) for p in pages]

    def listings_since(self, iso: str | None) -> list[dict]:
        f = {"timestamp": "last_edited_time", "last_edited_time": {"on_or_after": iso}} if iso else None
        return [self.flatten(p) for p in self.query(self.listings_db, filter=f)]

    # ---- buildings ----
    def upsert_building(self, b: dict) -> dict:
        props = {
            "Address": _title(b["address"]),
            "BBL": _rich(b["bbl"]),
            "Units": _number(b.get("units")),
            "Year built": _number(b.get("year_built")),
            "Bldg class": _rich(b.get("bldg_class")),
            "Owner": _rich(b.get("owner")),
            "Mgmt company": _rich(b.get("mgmt_company")),
            "Contact name": _rich(b.get("contact_name")),
            "Walk min": _number(b.get("walk_min")),
            "Lat": _number(b.get("lat")),
            "Lng": _number(b.get("lng")),
            "Past-rental evidence": _rich(b.get("evidence")),
            "Priority": _number(b.get("priority")),
        }
        existing = next(self.query(self.buildings_db, filter={"property": "BBL", "rich_text": {"equals": b["bbl"]}}, limit=1), None)
        if existing:
            return self.update(existing["id"], props)
        props["Outreach status"] = _select("Not started")
        return self._req("POST", "/pages", json={"parent": {"database_id": self.buildings_db}, "properties": props})

    def set_building_fields(self, page_id: str, **fields) -> dict:
        mapping = {
            "status": ("Outreach status", _select),
            "draft": ("Draft", _rich),
            "gmail_draft_id": ("Gmail draft ID", _rich),
            "notes": ("Notes", _rich),
            "contact_email": ("Contact email", lambda v: {"email": v or None}),
            "contact_phone": ("Contact phone", lambda v: {"phone_number": v or None}),
            "website": ("Website", lambda v: {"url": v or None}),
        }
        props = {}
        for key, value in fields.items():
            name, fn = mapping[key]
            props[name] = fn(value)
        return self.update(page_id, props)

    def buildings_since(self, iso: str | None) -> list[dict]:
        f = {"timestamp": "last_edited_time", "last_edited_time": {"on_or_after": iso}} if iso else None
        return [self.flatten(p) for p in self.query(self.buildings_db, filter=f)]
