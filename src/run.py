"""Poller entrypoint: ingest -> dedupe -> filter -> geo -> score -> Notion -> drafts + notifications.

    python -m src.run --once                 # one poll cycle (what GitHub Actions runs)
    python -m src.run --once --dry-run       # parse + score, print, write nothing
    python -m src.run --once --backfill 7    # include feed emails from the last 7 days
"""

import argparse
import json
import logging
import re
import time
import traceback
from datetime import datetime, timezone

from src import geo, notify, score
from src.config import STATE_DIR, cfg
from src.models import Listing

log = logging.getLogger("apt-hunt")

SEEN = STATE_DIR / "seen.json"
LAST_RUN = STATE_DIR / "last_run.json"
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def load_seen() -> dict:
    return json.loads(SEEN.read_text()) if SEEN.exists() else {}


def save_seen(seen: dict) -> None:
    # keep the newest 5000 keys
    items = sorted(seen.items(), key=lambda kv: kv[1], reverse=True)[:5000]
    SEEN.write_text(json.dumps(dict(items), indent=0, sort_keys=True))


def prefilter(l: Listing) -> str | None:
    """Return a rejection reason when known data already violates a hard requirement."""
    s = cfg()["search"]
    if l.price and l.price > s["max_gross_price"]:
        return f"price ${l.price}"
    if l.net_effective and l.net_effective > s["max_price"]:
        return f"net effective ${l.net_effective}"
    if l.price and l.price < 1200:
        return f"price ${l.price} (likely room share/scam)"
    if l.beds and l.beds not in s["beds"]:
        return f"beds {l.beds}"
    return None


def locate(l: Listing) -> None:
    if l.lat is None and l.address:
        hit = geo.geocode(l.address)
        if hit:
            l.lat, l.lng = hit
    if l.lat is not None:
        l.walk_min = geo.walk_minutes(l.lat, l.lng)


def collect(backfill_days: int, stats: dict, errors: list) -> tuple[list[Listing], list[str]]:
    listings: list[Listing] = []
    processed_email_ids: list[str] = []

    try:
        from src.ingest import gmail as gmail_in

        for email in gmail_in.fetch_unprocessed(days=backfill_days):
            try:
                found = score.extract_listings(email.source, email.subject, email.html, email.text)
                stats[f"email:{email.source}"] = stats.get(f"email:{email.source}", 0) + len(found)
                listings.extend(found)
                processed_email_ids.append(email.id)
            except Exception as e:  # one bad email shouldn't stop the run
                errors.append(f"extract {email.subject[:60]}: {e}")
    except Exception as e:
        errors.append(f"gmail ingest: {e}")

    try:
        from src.ingest import craigslist

        cl = craigslist.search()
        stats["craigslist"] = len(cl)
        listings.extend(cl)
    except Exception as e:
        errors.append(f"craigslist: {e}")

    try:
        from src.ingest import mgmt_sites

        for page in mgmt_sites.changed_pages():
            found = score.extract_listings("mgmt", page["name"], page["html"])
            stats[f"mgmt:{page['name']}"] = len(found)
            listings.extend(found)
    except Exception as e:
        errors.append(f"mgmt sites: {e}")

    return listings, processed_email_ids


def handle_hot(store, page: dict, listing: Listing, result: dict) -> None:
    from src import gmail_out

    item = notify.listing_item(page["id"], page.get("url"), listing, result)
    fields: dict = {"notified_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    if result.get("draft"):
        to = (EMAIL_RE.search(listing.contact or "") or [None])[0] or ""
        draft_id = gmail_out.create_draft(result.get("draft_subject") or listing.title, result["draft"], to=to)
        fields.update(gmail_draft_id=draft_id, status="Drafted")
    subject, html, text = notify.hot_email(item)
    gmail_out.send_notification(subject, html, text)
    store.set_listing_fields(page["id"], **fields)


def run_once(dry_run: bool = False, backfill_days: int = 2) -> dict:
    started = time.time()
    stats: dict = {}
    errors: list[str] = []
    seen = load_seen()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    listings, email_ids = collect(backfill_days, stats, errors)
    store = None
    if not dry_run:
        from src.notion_store import NotionStore

        store = NotionStore()
    examples = store.voted_examples() if store else []

    created = hot = rejected = dupes = 0
    batch_keys: set[str] = set()
    for l in listings:
        key = l.dedupe_key
        tkey = l.title_key
        if key in seen or key in batch_keys or (tkey and (tkey in seen or tkey in batch_keys)):
            dupes += 1
            continue
        batch_keys.update(k for k in (key, tkey) if k)
        seen[key] = now
        if tkey:
            seen[tkey] = now

        reason = prefilter(l)
        if not reason:
            locate(l)
            if l.walk_min is not None and l.walk_min > cfg()["search"]["max_walk_min"]:
                reason = f"walk {l.walk_min} min"
            elif l.walk_min is None and l.source == "craigslist":
                reason = "no location"
        if reason:
            rejected += 1
            log.debug("reject %s: %s", l.title[:60], reason)
            continue

        if store and store.find_listing(key, l.cross_source_key):
            dupes += 1
            continue

        if l.source == "craigslist" and not l.description and l.url:
            from src.ingest.craigslist import fetch_description

            l.description = fetch_description(l.url)

        try:
            result = score.score_listing(l, examples)
        except Exception as e:
            errors.append(f"score {l.title[:50]}: {e}")
            seen.pop(key, None)  # retry next run
            if tkey:
                seen.pop(tkey, None)
            continue

        if dry_run:
            print(f"[{result['category']:6}] {result['score']:>4} {l.source:11} ${l.price} {l.beds} {l.walk_min}min  {l.title[:70]}")
            print("        " + result["reasons"].replace("\n", "\n        "))
            continue

        page = store.create_listing(l, result)
        created += 1
        if result["category"] == "Hot":
            try:
                handle_hot(store, page, l, result)
                hot += 1
            except Exception as e:
                errors.append(f"notify {l.title[:50]}: {e}")

    if not dry_run:
        from src.ingest import gmail as gmail_in

        for mid in email_ids:
            try:
                gmail_in.mark_processed(mid)
            except Exception as e:
                errors.append(f"mark processed {mid}: {e}")
        save_seen(seen)

    summary = {
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seconds": round(time.time() - started, 1),
        "sources": stats,
        "candidates": len(listings),
        "duplicates": dupes,
        "rejected": rejected,
        "created": created,
        "hot": hot,
        "errors": errors,
        "dry_run": dry_run,
    }
    if not dry_run:
        LAST_RUN.write_text(json.dumps(summary, indent=1))
    log.info("run summary: %s", json.dumps(summary))
    return summary


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--once", action="store_true", help="run a single poll cycle")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--backfill", type=int, default=2, help="days of feed emails to consider")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "googleapiclient.discovery_cache", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    try:
        summary = run_once(dry_run=args.dry_run, backfill_days=args.backfill)
    except Exception:
        traceback.print_exc()
        raise
    if summary["errors"]:
        for e in summary["errors"]:
            log.error(e)


if __name__ == "__main__":
    main()
