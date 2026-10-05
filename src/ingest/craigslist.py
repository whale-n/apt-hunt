"""Craigslist apartments near the anchor via the JSON search endpoint the site itself uses (RSS is blocked)."""

import logging
from datetime import datetime, timezone

import httpx

from src.config import cfg
from src.models import Listing, normalize_beds

log = logging.getLogger(__name__)

SEARCH_URL = "https://sapi.craigslist.org/web/v8/postings/search/full"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"


def _image_url(ref: str) -> str:
    return f"https://images.craigslist.org/{ref.split(':', 1)[-1]}_600x450.jpg"


def parse_item(item: list, decode: dict) -> Listing | None:
    try:
        posting_id = decode["minPostingId"] + item[0]
        posted = datetime.fromtimestamp(decode["minPostedDate"] + item[1], tz=timezone.utc)
        price = item[3] if isinstance(item[3], int) and item[3] > 0 else None
        loc = item[4]
        loc_idx, rest = loc.split(":", 1)
        _, lat, lng = rest.split("~")
        location = decode["locations"][int(loc_idx.split(":")[0])] if loc_idx.isdigit() else None
        subarea = location[2] if isinstance(location, list) and len(location) > 2 else "brk"
    except (IndexError, KeyError, ValueError, TypeError):
        return None

    slug, photos, beds, sqft = "", [], None, None
    strings = []
    for el in item[5:]:
        if isinstance(el, list) and el:
            tag = el[0]
            if tag == 4:
                photos = [_image_url(r) for r in el[1:] if isinstance(r, str)]
            elif tag == 5 and len(el) >= 2:
                beds = normalize_beds(el[1])
                sqft = el[2] if len(el) > 2 and el[2] else None
            elif tag == 6 and len(el) > 1:
                slug = el[1]
        elif isinstance(el, str):
            strings.append(el)
    title = max(strings, key=len) if strings else ""
    url = f"https://newyork.craigslist.org/{subarea}/apa/d/{slug}/{posting_id}.html" if slug else ""
    return Listing(
        source="craigslist",
        url=url,
        title=title,
        price=price,
        beds=beds,
        sqft=sqft,
        lat=float(lat),
        lng=float(lng),
        photos=list(dict.fromkeys(photos)),
        posted_at=posted.isoformat(timespec="seconds"),
    )


def search() -> list[Listing]:
    s = cfg()["search"]
    params = {
        "batch": "3-0-360-0-0",
        "cc": "US",
        "lang": "en",
        "searchPath": "apa",
        "max_price": s["max_gross_price"],
        "postal": s["craigslist"]["postal"],
        "search_distance": s["craigslist"]["radius_mi"],
        "sort": "date",
    }
    r = httpx.get(SEARCH_URL, params=params, headers={"User-Agent": UA}, timeout=20)
    r.raise_for_status()
    data = r.json()["data"]
    decode = data["decode"]
    listings = [l for l in (parse_item(it, decode) for it in data.get("items", [])) if l]
    log.info("craigslist: %d listings", len(listings))
    return listings


def fetch_description(url: str) -> str:
    """Best-effort posting body; Craigslist sometimes blocks datacenter IPs, so failures are non-fatal."""
    from bs4 import BeautifulSoup

    try:
        r = httpx.get(url, headers={"User-Agent": UA}, timeout=15, follow_redirects=True)
        if r.status_code != 200:
            return ""
        body = BeautifulSoup(r.text, "html.parser").select_one("#postingbody")
        if not body:
            return ""
        for junk in body.select(".print-qrcode-container"):
            junk.decompose()
        return body.get_text(" ", strip=True)[:6000]
    except httpx.HTTPError:
        return ""
