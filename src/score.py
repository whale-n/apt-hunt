"""Claude-powered extraction (alert emails / web pages -> listings) and scoring + outreach drafting."""

import json
import logging
import re
from functools import lru_cache

import anthropic
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

from src.config import cfg
from src.models import Listing, normalize_beds

log = logging.getLogger(__name__)

MAX_EMAIL_CHARS = 40_000


@lru_cache
def client() -> anthropic.Anthropic:
    return anthropic.Anthropic()


def model() -> str:
    return cfg()["scoring"]["model"]


# ---------------------------------------------------------------- extraction

class ExtractedListing(BaseModel):
    url: str = Field(description="Direct link to this specific listing, exactly as it appears in the source")
    title: str = ""
    address: str = Field("", description="Street address if given, e.g. '150 N 7th St'")
    unit: str = ""
    neighborhood: str = ""
    price: int | None = Field(None, description="Gross / asking monthly rent in USD")
    net_effective: int | None = Field(None, description="Net effective monthly rent if stated (after free months)")
    beds: str | None = Field(None, description="'studio', '1', '2', '3' ...")
    baths: float | None = None
    sqft: int | None = None
    photos: list[str] = Field(default_factory=list, description="Image URLs belonging to this listing")
    description: str = ""
    available: str | None = Field(None, description="Move-in / availability date, ISO YYYY-MM-DD")
    contact: str = Field("", description="Agent/landlord name, email or phone if shown")


class Extraction(BaseModel):
    listings: list[ExtractedListing]


def html_to_marked_text(html: str) -> str:
    """Flatten HTML to text while keeping link targets and image sources visible to the model."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "head"]):
        tag.decompose()
    for img in soup.find_all("img"):
        src = img.get("src") or ""
        w = img.get("width")
        if src.startswith("http") and not (w and str(w).isdigit() and int(w) < 60):
            img.replace_with(f" [img {src}] ")
        else:
            img.decompose()
    for a in soup.find_all("a", href=True):
        a.replace_with(f" [{a.get_text(' ', strip=True)}]({a['href']}) ")
    text = soup.get_text(" ", strip=True)
    return re.sub(r"\s{2,}", " ", text)[:MAX_EMAIL_CHARS]


EXTRACT_SYSTEM = """You extract rental apartment listings from listing-alert emails and web pages.
Return every distinct rental listing in the content. Skip ads, saved-search summaries, footer links, and
non-rental items. Use only information present in the content; leave fields empty when unknown.
For Facebook group notifications, a post offering an apartment counts as a listing (use the post link as url);
posts from people seeking apartments or roommates do not."""


def extract_listings(source: str, subject: str, content_html: str = "", content_text: str = "") -> list[Listing]:
    body = html_to_marked_text(content_html) if content_html else content_text[:MAX_EMAIL_CHARS]
    if not body.strip():
        return []
    resp = client().messages.parse(
        model=model(),
        max_tokens=8000,
        system=EXTRACT_SYSTEM,
        messages=[{"role": "user", "content": f"Source: {source}\nSubject: {subject}\n\n{body}"}],
        output_format=Extraction,
    )
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        log.warning("extraction returned no output for %r (%s)", subject, resp.stop_reason)
        return []
    out = []
    for e in resp.parsed_output.listings:
        address = e.address
        if address and e.neighborhood and e.neighborhood.lower() not in address.lower():
            address = f"{address}, {e.neighborhood}"
        out.append(
            Listing(
                source=source,
                url=e.url,
                title=e.title,
                address=address,
                unit=e.unit,
                price=e.price,
                net_effective=e.net_effective,
                beds=normalize_beds(e.beds),
                baths=e.baths,
                sqft=e.sqft,
                photos=e.photos,
                description=e.description,
                available=e.available,
                contact=e.contact,
            )
        )
    return out


# ---------------------------------------------------------------- scoring

TAGS = ["loft", "high-ceilings", "light", "w/d", "dishwasher", "outdoor", "no-fee", "fee-flag", "leasebreak", "spacious"]


class ScoreResult(BaseModel):
    move_in_ok: bool = Field(
        description="True unless the listing explicitly states an availability date outside the move-in window. "
        "Missing or vague availability counts as True."
    )
    score: float = Field(description="0-10 fit score")
    tags: list[str] = Field(description=f"Subset of: {', '.join(TAGS)}")
    reasons: str = Field(description="2-4 short bullet points (start each with '- ') explaining the score")
    est_sqft: int | None = Field(None, description="Square footage if stated or reasonably inferable, else null")
    net_effective: int | None = Field(
        None,
        description="Net effective monthly rent: stated value, or computed from concessions as "
        "gross * (lease months - free months) / lease months. Null if no concession is mentioned.",
    )
    concession: str = Field("", description="Short description of any concession, e.g. '1 month free on 13-mo lease'")
    draft_subject: str = Field(description="Subject line for an inquiry email about this listing")
    draft_body: str = Field(description="Short, warm inquiry email body (under 120 words), signed with the renter's name")


@lru_cache
def score_system() -> str:
    c = cfg()
    s, sc, p = c["search"], c["scoring"], c["profile"]
    priorities = "\n".join(f"{i + 1}. {x}" for i, x in enumerate(sc["priorities"]))
    return f"""You evaluate NYC rental listings for one renter and draft an inquiry email for each.

Hard requirements (already pre-filtered where data allowed; flag anything that slipped through):
- Net effective rent at most ${s['max_price']:,}/month. Asking rents up to ${s['max_gross_price']:,} qualify only
  when a concession (free months, owner-paid fee credit) brings the net effective to ${s['max_price']:,} or less.
- Studio (large), 1BR or 2BR
- Within a {s['max_walk_min']}-minute walk of the {s['anchor']['name']} station in north Williamsburg, Brooklyn
- Move-in between {s['move_in']['earliest']} and {s['move_in']['latest']} (target {p['move_in_window']})

What the renter cares about, most important first:
{priorities}

Scoring guidance (0-10):
- 9-10: hits high ceilings AND light AND space, plus in-unit W/D or outdoor space. Act immediately.
- 7-8: strong on most priorities with no major red flags.
- 5-6: acceptable but missing several priorities, or too little information to judge.
- 0-4: cramped, dark, basement/garden-level with poor light, or violates a requirement.
Missing information (move-in date, sqft, amenities) is not a reason to reject: score what is known and note
what to ask. Shared roof decks count as outdoor access but less than private outdoor space.
Judge ceilings, light and spaciousness from photos when provided; say when evidence is missing rather than assuming.
Studios need concrete evidence of size (sqft >= ~500, separate sleeping area, or photos) to score above 6.
Under NYC's FARE Act (2025), a tenant generally cannot be charged a broker fee when the broker works for the landlord.
Tag "fee-flag" when a listing says the tenant pays a broker fee; tag "no-fee" when it explicitly says no fee.

Inquiry draft: address the agent/landlord by name if known, reference the specific unit and one detail you liked,
state the move-in window, include this profile briefly, and ask to schedule a viewing.
Renter: {p['name']}. {p['blurb']} {('Phone: ' + p['phone']) if p.get('phone') else ''}"""


def _examples_block(examples: list[dict]) -> str:
    if not examples:
        return ""
    lines = []
    for e in examples:
        verdict = "LIKED" if e.get("My vote") == "up" else "DISLIKED"
        summary = f"{e.get('Title')} | ${e.get('Price')} | {e.get('Beds')} | tags: {', '.join(e.get('Tags') or [])}"
        note = (e.get("Notes") or "").strip()
        lines.append(f"- {verdict}: {summary}" + (f" | renter note: {note}" if note else ""))
    return "Recent listings the renter voted on (calibrate to their taste):\n" + "\n".join(lines) + "\n\n"


def _listing_text(l: Listing) -> str:
    fields = {
        "Source": l.source,
        "Title": l.title,
        "Address": l.address,
        "Unit": l.unit,
        "Rent": f"${l.price:,}" if l.price else "unknown",
        "Net effective (stated)": f"${l.net_effective:,}" if l.net_effective else None,
        "Beds": l.beds or "unknown",
        "Baths": l.baths,
        "Sqft": l.sqft or "not stated",
        "Walk to station": f"{l.walk_min} min" if l.walk_min is not None else "unknown",
        "Available": l.available or "not stated",
        "Contact": l.contact,
        "URL": l.url,
        "Description": l.description or "(none)",
    }
    return "\n".join(f"{k}: {v}" for k, v in fields.items() if v not in (None, ""))


def score_listing(listing: Listing, examples: list[dict] | None = None) -> dict:
    sc = cfg()["scoring"]
    text = _examples_block(examples or []) + "Listing to evaluate:\n" + _listing_text(listing)
    images = [
        {"type": "image", "source": {"type": "url", "url": u}}
        for u in listing.photos[: sc["max_photos"]]
        if u.startswith("https://")
    ]

    def call(with_images: bool):
        content = (images if with_images else []) + [{"type": "text", "text": text}]
        return client().messages.parse(
            model=model(),
            max_tokens=2000,
            system=[{"type": "text", "text": score_system(), "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": content}],
            output_format=ScoreResult,
        )

    try:
        resp = call(with_images=bool(images))
    except anthropic.BadRequestError as e:
        if not images:
            raise
        log.info("image fetch rejected (%s); rescoring without photos", e.message[:120])
        resp = call(with_images=False)

    r = resp.parsed_output
    if r is None:
        return {"score": 0, "category": "Maybe", "tags": [], "reasons": f"Scoring unavailable ({resp.stop_reason})"}
    score = max(0.0, min(10.0, r.score))
    reasons = r.reasons
    if not r.move_in_ok:
        score = min(score, 3.0)
    s = cfg()["search"]
    net = listing.net_effective or r.net_effective
    if listing.price and listing.price > s["max_price"] and not (net and net <= s["max_price"]):
        score = min(score, 3.0)
        reasons = f"- Over ${s['max_price']:,} with no concession bringing net effective under budget\n" + reasons
    tags = [t for t in r.tags if t in TAGS]
    if r.concession and "concession" not in tags:
        tags.append("concession")
    return {
        "score": round(score, 1),
        "category": categorize(score),
        "tags": tags,
        "reasons": reasons,
        "net_effective": net,
        "concession": r.concession,
        "est_sqft": r.est_sqft,
        "draft_subject": r.draft_subject,
        "draft": r.draft_body,
    }


def categorize(score: float) -> str:
    sc = cfg()["scoring"]
    if score >= sc["hot_threshold"]:
        return "Hot"
    if score >= sc["good_threshold"]:
        return "Good"
    if score >= sc["maybe_threshold"]:
        return "Maybe"
    return "Reject"


# ---------------------------------------------------------------- building outreach

class BuildingDraft(BaseModel):
    subject: str
    body: str
    phone_script: str = Field(description="3-5 sentence script for calling the super or office")


def draft_building_outreach(building: dict) -> dict:
    p = cfg()["profile"]
    s = cfg()["search"]
    prompt = f"""Write a short, friendly cold email to a NYC building's management asking about upcoming rental vacancies.

Building: {building.get('Address')} (Williamsburg, Brooklyn), {building.get('Units') or '?'} units, built {building.get('Year built') or '?'}
Management company: {building.get('Mgmt company') or 'unknown'}
Contact: {building.get('Contact name') or 'unknown'}
Known rental history: {building.get('Past-rental evidence') or 'none recorded'}

Ask whether any studio/1BR/2BR units (up to ${s['max_price']:,}/mo) are opening for a {p['move_in_window']} move-in,
and ask to be added to their waitlist or vacancy list. Mention why this building specifically appeals (location near
the Bedford L; character of the building if inferable). Keep it under 110 words, signed by {p['name']}.
Renter profile: {p['blurb']}"""
    resp = client().messages.parse(
        model=model(),
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
        output_format=BuildingDraft,
    )
    d = resp.parsed_output
    if d is None:
        raise RuntimeError(f"draft generation failed ({resp.stop_reason})")
    return {"subject": d.subject, "body": d.body, "phone_script": d.phone_script}


def debug_dump(obj) -> str:
    return json.dumps(obj, indent=2, default=str)
