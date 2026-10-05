"""Geocoding (NYC Planning Labs GeoSearch) and walking time to the anchor station (OSRM foot profile)."""

import logging
import math

import httpx

from src.config import cfg

log = logging.getLogger(__name__)

WALK_M_PER_MIN = 80.0  # ~3 mph
DETOUR_FACTOR = 1.25  # street-grid detour over straight-line distance
GEOSEARCH_URL = "https://geosearch.planninglabs.nyc/v2/search"
OSRM_FOOT_URL = "https://routing.openstreetmap.de/routed-foot/route/v1/foot"

_http = httpx.Client(timeout=10, headers={"User-Agent": "apt-hunt/0.1 (personal apartment search)"})


def anchor() -> tuple[float, float]:
    a = cfg()["search"]["anchor"]
    return a["lat"], a["lng"]


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def estimate_walk_min(lat: float, lng: float) -> float:
    alat, alng = anchor()
    return haversine_m(lat, lng, alat, alng) * DETOUR_FACTOR / WALK_M_PER_MIN


def walk_minutes(lat: float, lng: float) -> float:
    """Routed walking minutes to the anchor; falls back to a detour-adjusted straight-line estimate."""
    estimate = estimate_walk_min(lat, lng)
    if estimate > 20:  # clearly out of range, don't spend a routing call
        return round(estimate, 1)
    alat, alng = anchor()
    try:
        r = _http.get(f"{OSRM_FOOT_URL}/{lng},{lat};{alng},{alat}", params={"overview": "false"})
        r.raise_for_status()
        data = r.json()
        if data.get("code") == "Ok":
            # OSRM foot speed is ~5 km/h; use distance at our walking pace for consistency.
            return round(data["routes"][0]["distance"] / WALK_M_PER_MIN, 1)
    except (httpx.HTTPError, KeyError, ValueError) as e:
        log.warning("OSRM routing failed (%s); using estimate", e)
    return round(estimate, 1)


def geocode(address: str) -> tuple[float, float] | None:
    if not address:
        return None
    text = address if "brooklyn" in address.lower() or "ny" in address.lower() else f"{address}, Brooklyn, NY"
    try:
        r = _http.get(GEOSEARCH_URL, params={"text": text, "size": 1})
        r.raise_for_status()
        features = r.json().get("features") or []
    except (httpx.HTTPError, ValueError) as e:
        log.warning("Geocode failed for %r: %s", address, e)
        return None
    if not features:
        return None
    lng, lat = features[0]["geometry"]["coordinates"]
    return lat, lng


def walk_circle(minutes: float | None = None, points: int = 48) -> list[list[float]]:
    """Approximate walk-radius polygon ([lat, lng] pairs) for map display."""
    minutes = minutes or cfg()["search"]["max_walk_min"]
    radius_m = minutes * WALK_M_PER_MIN / DETOUR_FACTOR
    alat, alng = anchor()
    out = []
    for i in range(points + 1):
        theta = 2 * math.pi * i / points
        dlat = radius_m * math.cos(theta) / 111_320
        dlng = radius_m * math.sin(theta) / (111_320 * math.cos(math.radians(alat)))
        out.append([alat + dlat, alng + dlng])
    return out
