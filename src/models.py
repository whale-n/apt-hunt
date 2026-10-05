import hashlib
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit


@dataclass
class Listing:
    source: str
    url: str = ""
    title: str = ""
    address: str = ""
    unit: str = ""
    price: int | None = None
    beds: str | None = None  # studio | 1br | 2br | 3br+
    baths: float | None = None
    sqft: int | None = None
    lat: float | None = None
    lng: float | None = None
    walk_min: float | None = None
    photos: list[str] = field(default_factory=list)
    description: str = ""
    available: str | None = None  # ISO date
    posted_at: str | None = None  # ISO datetime
    contact: str = ""

    @property
    def dedupe_key(self) -> str:
        """Stable identity across sources: the listing URL path when present, else address+unit+price."""
        if self.url:
            parts = urlsplit(self.url)
            basis = f"{parts.netloc.removeprefix('www.')}{parts.path.rstrip('/')}".lower()
        else:
            basis = f"{norm_address(self.address)}|{self.unit.lower().strip()}|{self.price}"
        return hashlib.sha1(basis.encode()).hexdigest()[:16]

    @property
    def cross_source_key(self) -> str | None:
        """Matches the same unit posted on two different sites."""
        if not self.address or not self.price:
            return None
        basis = f"{norm_address(self.address)}|{self.unit.lower().strip()}|{self.price}"
        return hashlib.sha1(basis.encode()).hexdigest()[:16]


    @property
    def title_key(self) -> str | None:
        """Catches reposts of the same ad under a new URL (common on Craigslist)."""
        if self.source != "craigslist":
            return None
        t = re.sub(r"[^a-z0-9]", "", self.title.lower())
        if len(t) < 15:
            return None
        return hashlib.sha1(f"{self.source}|{t}".encode()).hexdigest()[:16]


def norm_address(address: str) -> str:
    a = address.lower()
    a = re.sub(r",.*$", "", a)
    a = re.sub(r"\b(north)\b", "n", a)
    a = re.sub(r"\b(south)\b", "s", a)
    a = re.sub(r"\b(street|st\.)\b", "st", a)
    a = re.sub(r"\b(avenue|ave\.)\b", "ave", a)
    a = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", a)
    return re.sub(r"\s+", " ", a).strip()


def normalize_beds(value) -> str | None:
    if value is None:
        return None
    s = str(value).strip().lower()
    if s in {"0", "studio", "0br", "loft studio"} or "studio" in s:
        return "studio"
    m = re.match(r"(\d+)", s)
    if not m:
        return None
    n = int(m.group(1))
    if n == 0:
        return "studio"
    return f"{n}br" if n <= 2 else "3br+"
