import pytest

from src import geo
from src.gmail_out import RecipientNotAllowed, check_notification_recipient
from src.ingest.craigslist import parse_item
from src.models import Listing, normalize_beds, norm_address
from src.run import prefilter

N7TH_BEDFORD_BERRY = (40.71848, -73.95813)  # N 7th St between Bedford Ave & Berry St
GRAHAM_AVE = (40.70888, -73.94336)  # Graham Ave & Grand St, East Williamsburg


def test_walk_estimate_inside_and_outside():
    assert geo.estimate_walk_min(*N7TH_BEDFORD_BERRY) < 4
    assert geo.estimate_walk_min(*GRAHAM_AVE) > 15


def test_notification_recipient_guard(monkeypatch):
    from src.config import cfg

    monkeypatch.setitem(cfg()["notify"], "to", "me@example.com")
    assert check_notification_recipient("Me@Example.com ") == "me@example.com"
    for bad in ("landlord@example.com", "me+x@example.com", ""):
        with pytest.raises(RecipientNotAllowed):
            check_notification_recipient(bad)


def test_normalize_beds():
    assert normalize_beds("Studio") == "studio"
    assert normalize_beds(0) == "studio"
    assert normalize_beds("1") == "1br"
    assert normalize_beds("2 bed") == "2br"
    assert normalize_beds(4) == "3br+"
    assert normalize_beds(None) is None


def test_dedupe_keys():
    a = Listing(source="streeteasy", url="https://streeteasy.com/building/x/3a?utm=1")
    b = Listing(source="streeteasy", url="https://www.streeteasy.com/building/x/3a/")
    assert a.dedupe_key == b.dedupe_key
    c = Listing(source="zillow", address="150 North 7th Street, Brooklyn", unit="3A", price=4500)
    d = Listing(source="renthop", address="150 N 7 St", unit="3a", price=4500)
    assert c.cross_source_key == d.cross_source_key
    assert norm_address("150 North 7th Street") == "150 n 7 st"


def test_prefilter():
    assert prefilter(Listing(source="x", price=5600))  # over gross ceiling
    assert prefilter(Listing(source="x", price=5400, net_effective=5100))  # concession not enough
    assert prefilter(Listing(source="x", price=5400)) is None  # may have a concession; scorer decides
    assert prefilter(Listing(source="x", price=5400, net_effective=4985)) is None
    assert prefilter(Listing(source="x", price=4000, beds="3br+"))
    assert prefilter(Listing(source="x", price=4800, beds="1br")) is None


def test_parse_craigslist_item():
    decode = {"minPostingId": 7960559757, "minPostedDate": 1788525259,
              "locations": [0, [3, "newyork", "brk"]]}
    item = [17443989, 2624791, 1, 4695, "1:2~40.719~-73.9425", "0mW0cU", [13, "x"],
            [4, "3:01111_cmfqDDLp9ul_0mW0cU"], [6, "brooklyn-sunny-willy-gem2"], [10, "$4,695"],
            "SUNNY WILLY B GEM!2 BED!LAUNDRY&GYM", [5, 2, 800]]
    l = parse_item(item, decode)
    assert l.price == 4695 and l.beds == "2br" and l.sqft == 800
    assert l.url == "https://newyork.craigslist.org/brk/apa/d/brooklyn-sunny-willy-gem2/7978003746.html"
    assert l.photos == ["https://images.craigslist.org/01111_cmfqDDLp9ul_0mW0cU_600x450.jpg"]
    assert l.title.startswith("SUNNY")
