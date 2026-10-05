"""Twice-daily digest of Good listings that haven't been notified yet."""

import logging
from datetime import datetime, timedelta, timezone

from src import gmail_out, notify
from src.models import Listing
from src.notion_store import NotionStore

log = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    store = NotionStore()
    since = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    pages = store.query(
        store.listings_db,
        filter={
            "and": [
                {"property": "Category", "select": {"equals": "Good"}},
                {"property": "Notified at", "date": {"is_empty": True}},
                {"property": "Status", "select": {"does_not_equal": "Dead"}},
                {"timestamp": "created_time", "created_time": {"on_or_after": since}},
            ]
        },
    )
    items = []
    for page in pages:
        f = store.flatten(page)
        l = Listing(
            source=f.get("Source") or "",
            url=f.get("URL") or "",
            title=f.get("Title") or "",
            address=f.get("Address") or "",
            price=int(f["Price"]) if f.get("Price") else None,
            beds=f.get("Beds"),
            walk_min=f.get("Walk min"),
            photos=(f.get("Photos") or "").split(),
        )
        items.append(notify.listing_item(f["id"], f.get("notion_url"), l, {"score": f.get("Score") or 0, "tags": f.get("Tags") or [], "reasons": f.get("Score reasons") or ""}))
    if not items:
        log.info("digest: nothing new")
        return
    gmail_out.send_notification(*notify.digest_email(items))
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for i in items:
        store.set_listing_fields(i["page_id"], notified_at=stamp)
    log.info("digest: sent %d", len(items))


if __name__ == "__main__":
    main()
