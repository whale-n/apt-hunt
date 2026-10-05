"""Write outreach drafts (email + phone script) for the top-priority buildings into Notion. Nothing is sent.

    python -m src.offmarket.drafts --top 20
"""

import argparse
import logging

from src.notion_store import NotionStore
from src.score import draft_building_outreach

log = logging.getLogger(__name__)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=20)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    for noisy in ("httpx", "httpx2", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    store = NotionStore()
    buildings = [store.flatten(p) for p in store.query(
        store.buildings_db,
        filter={"property": "Outreach status", "select": {"equals": "Not started"}},
        sorts=[{"property": "Priority", "direction": "descending"}],
        limit=args.top,
    )]
    done = 0
    for b in buildings:
        if b.get("Draft"):
            continue
        try:
            d = draft_building_outreach(b)
        except Exception as e:
            log.warning("draft failed for %s: %s", b.get("Address"), e)
            continue
        text = f"Subject: {d['subject']}\n\n{d['body']}\n\n--- Phone script ---\n{d['phone_script']}"
        store.set_building_fields(b["id"], draft=text)
        done += 1
        log.info("drafted %s", b.get("Address"))
    print(f"Wrote {done} building drafts to Notion")


if __name__ == "__main__":
    main()
