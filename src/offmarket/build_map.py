"""Off-market building map: every multifamily rental building within the walk radius, with owner/manager contacts.

    python -m src.offmarket.build_map              # build + write top buildings to Notion
    python -m src.offmarket.build_map --dry-run    # print the ranking only
    python -m src.offmarket.build_map --top 150

Data: NYC PLUTO (lots, units, year built, class) + HPD Multiple Dwelling Registrations / Registration Contacts
(managing agent, owner). Writes state/buildings.json (full ranked list) and state/mgmt_companies.json (rollup).
"""

import argparse
import json
import logging
from collections import defaultdict

import httpx

from src import geo
from src.config import STATE_DIR, cfg
from src.models import norm_address

log = logging.getLogger(__name__)

SODA = "https://data.cityofnewyork.us/resource"
PLUTO = "64uk-42ks"
HPD_REG = "tesw-yqqr"
HPD_CONTACTS = "feu5-w2e2"

_http = httpx.Client(timeout=60)


def soda(dataset: str, params: dict) -> list[dict]:
    rows, offset = [], 0
    while True:
        page = _http.get(f"{SODA}/{dataset}.json", params={**params, "$limit": 5000, "$offset": offset}).json()
        if isinstance(page, dict) and page.get("error"):
            raise RuntimeError(page)
        rows += page
        if len(page) < 5000:
            return rows
        offset += 5000


def fetch_lots(max_walk_min: float) -> list[dict]:
    alat, alng = geo.anchor()
    d = 0.012  # ~1.3km box; trimmed by walk estimate below
    rows = soda(
        PLUTO,
        {
            "$select": "bbl,address,unitsres,unitstotal,yearbuilt,yearalter1,bldgclass,ownername,numfloors,latitude,longitude",
            "$where": (
                f"borough='BK' AND unitsres >= 3 AND latitude between {alat - d} and {alat + d} "
                f"AND longitude between {alng - d * 1.3} and {alng + d * 1.3}"
            ),
        },
    )
    lots = []
    for r in rows:
        try:
            lat, lng = float(r["latitude"]), float(r["longitude"])
        except (KeyError, ValueError):
            continue
        walk = geo.estimate_walk_min(lat, lng)
        if walk > max_walk_min:
            continue
        bbl = str(int(float(r["bbl"])))
        lots.append(
            {
                "bbl": bbl,
                "address": r.get("address", "").title(),
                "units": int(float(r.get("unitsres") or 0)),
                "year_built": int(float(r.get("yearbuilt") or 0)) or None,
                "year_altered": int(float(r.get("yearalter1") or 0)) or None,
                "bldg_class": r.get("bldgclass", ""),
                "floors": float(r.get("numfloors") or 0),
                "owner": r.get("ownername", ""),
                "lat": lat,
                "lng": lng,
                "walk_min": round(walk, 1),
            }
        )
    log.info("PLUTO: %d lots within %s min", len(lots), max_walk_min)
    return lots


def attach_hpd(lots: list[dict]) -> None:
    by_bb = {(l["bbl"][1:6].lstrip("0"), l["bbl"][6:].lstrip("0")): l for l in lots}
    blocks = sorted({b for b, _ in by_bb})
    regs = []
    for i in range(0, len(blocks), 80):
        chunk = ",".join(f"'{b}'" for b in blocks[i : i + 80])
        regs += soda(HPD_REG, {"$select": "registrationid,block,lot,lastregistrationdate", "$where": f"boroid='3' AND block in ({chunk})"})
    reg_to_lot = {}
    for r in sorted(regs, key=lambda r: r.get("lastregistrationdate", "")):
        lot = by_bb.get((r["block"].lstrip("0"), r["lot"].lstrip("0")))
        if lot:
            reg_to_lot[r["registrationid"]] = lot  # newest registration wins
    ids = list(reg_to_lot)
    contacts = []
    for i in range(0, len(ids), 150):
        chunk = ",".join(f"'{x}'" for x in ids[i : i + 150])
        contacts += soda(HPD_CONTACTS, {"$where": f"registrationid in ({chunk})"})
    for c in contacts:
        lot = reg_to_lot.get(c["registrationid"])
        if not lot:
            continue
        name = " ".join(x for x in (c.get("firstname"), c.get("lastname")) if x).title()
        corp = (c.get("corporationname") or "").title()
        addr = " ".join(x for x in (c.get("businesshousenumber"), c.get("businessstreetname"), c.get("businesscity"), c.get("businessstate"), c.get("businesszip")) if x).title()
        t = c.get("type")
        if t == "Agent":
            lot["mgmt_company"] = corp or lot.get("mgmt_company") or name
            lot["contact_name"] = name or lot.get("contact_name")
            lot["mgmt_address"] = addr
        elif t == "SiteManager":
            lot["site_manager"] = name
        elif t in ("CorporateOwner", "IndividualOwner") and not lot.get("owner_contact"):
            lot["owner_contact"] = corp or name
        elif t == "HeadOfficer" and not lot.get("head_officer"):
            lot["head_officer"] = name
    log.info("HPD: %d registrations, %d contacts", len(reg_to_lot), len(contacts))


def attach_evidence(lots: list[dict]) -> None:
    """Count past listings we've seen at each address (from Notion), the strongest 'they rent here' signal."""
    try:
        from src.notion_store import NotionStore

        listings = NotionStore().listings_since(None)
    except Exception as e:
        log.warning("skipping Notion evidence: %s", e)
        return
    by_addr = defaultdict(list)
    for l in listings:
        if l.get("Address"):
            by_addr[norm_address(l["Address"])].append(l)
    for lot in lots:
        hits = by_addr.get(norm_address(lot["address"]), [])
        if hits:
            lot["evidence"] = "; ".join(f"{h.get('Beds') or '?'} ${int(h['Price']) if h.get('Price') else '?'} ({h.get('Source')})" for h in hits[:6])
            lot["evidence_count"] = len(hits)


def priority(lot: dict) -> float:
    p = 0.0
    units = lot["units"]
    p += 3 if 4 <= units <= 40 else 2 if units <= 120 else 1  # small/mid buildings have accessible landlords
    cls = lot["bldg_class"][:1]
    if cls in ("C", "D"):  # walk-up / elevator apartments
        p += 2
    if cls == "S":  # mixed residential + retail (common in Williamsburg)
        p += 1
    yb = lot.get("year_built") or 0
    if yb and yb < 1940 and (lot.get("year_altered") or 0) >= 1990:
        p += 2  # older industrial/loft stock that was converted
    if lot["bldg_class"].startswith("R"):
        p -= 1  # condo units: individual owners, harder to reach
    if lot.get("mgmt_company"):
        p += 1
    p += min(3, 1.5 * lot.get("evidence_count", 0))
    p += max(0, (10 - lot["walk_min"]) / 5)  # closer is better
    return round(p, 2)


def rollup(lots: list[dict]) -> list[dict]:
    cos = defaultdict(lambda: {"buildings": 0, "units": 0, "addresses": []})
    for l in lots:
        name = l.get("mgmt_company")
        if not name:
            continue
        c = cos[name]
        c["name"] = name
        c["buildings"] += 1
        c["units"] += l["units"]
        c["addresses"].append(l["address"])
        c["mgmt_address"] = l.get("mgmt_address", "")
    return sorted(cos.values(), key=lambda c: -c["units"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--top", type=int, default=150)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    lots = fetch_lots(cfg()["search"]["max_walk_min"])
    attach_hpd(lots)
    attach_evidence(lots)
    for l in lots:
        l["priority"] = priority(l)
    lots.sort(key=lambda l: -l["priority"])
    companies = rollup(lots)

    (STATE_DIR / "buildings.json").write_text(json.dumps(lots, indent=1))
    (STATE_DIR / "mgmt_companies.json").write_text(json.dumps(companies, indent=1))
    print(f"{len(lots)} buildings, {len(companies)} management companies")
    print("\nTop management companies by units in the zone:")
    for c in companies[:15]:
        print(f"  {c['units']:>5} units / {c['buildings']:>3} bldgs  {c['name']}")
    print("\nTop buildings:")
    for l in lots[:20]:
        print(f"  {l['priority']:>5}  {l['address']:<28} {l['units']:>4}u  {l['bldg_class']:<3} {l.get('year_built') or '':<5} {l['walk_min']:>4}min  {l.get('mgmt_company') or l['owner']}")

    if args.dry_run:
        return
    from src.notion_store import NotionStore

    store = NotionStore()
    for l in lots[: args.top]:
        store.upsert_building({**l, "owner": l.get("owner_contact") or l["owner"], "contact_name": l.get("contact_name") or l.get("site_manager") or l.get("head_officer")})
    print(f"\nWrote top {min(args.top, len(lots))} buildings to Notion")


if __name__ == "__main__":
    main()
