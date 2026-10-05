"""Watch management-company / broker listing pages and surface pages whose listing links changed."""

import hashlib
import json
import logging

import httpx
from bs4 import BeautifulSoup

from src.config import STATE_DIR, cfg

log = logging.getLogger(__name__)

STATE = STATE_DIR / "mgmt_seen.json"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"


def _load() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def changed_pages() -> list[dict]:
    """Return [{name, url, text}] for configured pages that gained new links since last run."""
    seen = _load()
    out = []
    for site in cfg().get("mgmt_sites") or []:
        try:
            r = httpx.get(site["url"], headers={"User-Agent": UA}, timeout=20, follow_redirects=True)
            r.raise_for_status()
        except httpx.HTTPError as e:
            log.warning("mgmt site %s failed: %s", site["name"], e)
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        links = sorted({a["href"] for a in soup.find_all("a", href=True)})
        link_hashes = {hashlib.sha1(l.encode()).hexdigest()[:12] for l in links}
        previous = set(seen.get(site["url"], []))
        new = link_hashes - previous
        seen[site["url"]] = sorted(link_hashes)
        if previous and new:
            out.append({"name": site["name"], "url": site["url"], "html": r.text})
        elif not previous:
            log.info("mgmt site %s: baseline recorded (%d links)", site["name"], len(links))
    STATE.write_text(json.dumps(seen, indent=1))
    return out
