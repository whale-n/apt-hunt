"""Local tracker UI: uvicorn ui.app:app --port 8765"""

import json
import logging
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src import geo
from src.config import STATE_DIR, cfg
from src.notion_store import NotionStore
from ui.sync import Cache, key

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)

HERE = Path(__file__).parent
STATUSES = ["New", "Shortlist", "Drafted", "Contacted", "Viewing", "Applied", "Dead"]
BUILDING_STATUSES = ["Not started", "Drafted", "Contacted", "Replied", "Waitlisted", "No vacancies"]

app = FastAPI(title="Apt Hunt")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")

store = NotionStore()
cache = Cache(store)


@app.on_event("startup")
def _startup() -> None:
    try:
        cache.pull()
    except Exception as e:
        cache.last_error = str(e)
    cache.start()


# ------------------------------------------------------------------ helpers

def photos(item: dict) -> list[str]:
    return [p for p in (item.get("Photos") or "").split() if p.startswith("http")]


def today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def counts() -> dict:
    ls = cache.listings()
    week = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    return {
        "new_today": sum(1 for l in ls if (l.get("created_time") or "").startswith(today_iso())),
        "hot": sum(1 for l in ls if l.get("Category") == "Hot" and l.get("Status") not in ("Dead",)),
        "awaiting": sum(1 for l in ls if l.get("Status") == "Contacted"),
        "viewings": sum(1 for l in ls if l.get("Viewing at") and today_iso() <= l["Viewing at"] <= week),
        "inbox": sum(1 for l in ls if l.get("Status") == "New"),
    }


def render(request: Request, name: str, **ctx) -> HTMLResponse:
    ctx.update(request=request, counts=counts(), sync_error=cache.last_error, photos=photos, key=key, statuses=STATUSES)
    return templates.TemplateResponse(request, name, ctx)


def by_score(items: list[dict]) -> list[dict]:
    return sorted(items, key=lambda l: (-(l.get("Score") or 0), l.get("created_time") or ""))


def gh(*args: str, timeout: int = 20) -> str:
    out = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=timeout)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or "gh failed")
    return out.stdout


_gh_cache: dict = {}


def gh_cached(name: str, fn, ttl: int = 60):
    hit = _gh_cache.get(name)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    try:
        value = fn()
    except Exception as e:
        value = {"error": str(e)}
    _gh_cache[name] = (time.time(), value)
    return value


# ------------------------------------------------------------------ listings

@app.get("/", response_class=HTMLResponse)
def triage(request: Request, show: str = "new"):
    ls = cache.listings()
    if show == "new":
        items = [l for l in ls if l.get("Status") == "New" and l.get("Category") != "Reject"]
    elif show == "rejects":
        items = [l for l in ls if l.get("Status") == "New" and l.get("Category") == "Reject"]
    else:
        items = [l for l in ls if l.get("Status") != "Dead"]
    return render(request, "triage.html", items=by_score(items), show=show)


@app.post("/l/{page_id}/vote/{vote}", response_class=HTMLResponse)
def vote(page_id: str, vote: str):
    status = "Shortlist" if vote == "up" else "Dead"
    store.set_listing_fields(page_id, vote=vote, status=status)
    cache.refresh_listing(page_id)
    return HTMLResponse("")  # HTMX removes the card


@app.post("/l/{page_id}/status")
def set_status(page_id: str, status: str = Form(...)):
    if status not in STATUSES:
        return Response(status_code=400)
    store.set_listing_fields(page_id, status=status)
    cache.refresh_listing(page_id)
    return Response(status_code=204)


@app.get("/l/{page_id}", response_class=HTMLResponse)
def detail(request: Request, page_id: str, saved: str = ""):
    item = cache.listing(page_id) or cache.refresh_listing(page_id)
    return render(request, "detail.html", l=item, saved=saved)


@app.post("/l/{page_id}/save")
def save_listing(page_id: str, draft: str = Form(""), notes: str = Form(""), viewing_at: str = Form("")):
    store.set_listing_fields(page_id, draft=draft, notes=notes, viewing_at=viewing_at or None)
    cache.refresh_listing(page_id)
    return RedirectResponse(f"/l/{page_id}?saved=1", status_code=303)


@app.post("/l/{page_id}/gmail-draft")
def listing_gmail_draft(page_id: str, draft: str = Form(...), to: str = Form("")):
    from src import gmail_out

    item = cache.listing(page_id) or {}
    subject = f"Inquiry: {item.get('Title') or 'your listing'}"
    draft_id = gmail_out.create_draft(subject, draft, to=to.strip())
    store.set_listing_fields(page_id, draft=draft, gmail_draft_id=draft_id, status="Drafted")
    cache.refresh_listing(page_id)
    return RedirectResponse(f"/l/{page_id}?saved=draft", status_code=303)


@app.get("/board", response_class=HTMLResponse)
def board(request: Request):
    ls = cache.listings()
    columns = {s: by_score([l for l in ls if l.get("Status") == s]) for s in STATUSES}
    columns["New"] = [l for l in columns["New"] if l.get("Category") in ("Hot", "Good")]
    return render(request, "board.html", columns=columns)


# ------------------------------------------------------------------ map

@app.get("/map", response_class=HTMLResponse)
def map_view(request: Request):
    return render(request, "map.html", anchor=cfg()["search"]["anchor"], circle=json.dumps(geo.walk_circle()))


@app.get("/api/points")
def points():
    ls = [
        {"id": key(l["id"]), "lat": l["Lat"], "lng": l["Lng"], "title": l.get("Title"), "price": l.get("Price"),
         "beds": l.get("Beds"), "score": l.get("Score"), "status": l.get("Status"), "category": l.get("Category")}
        for l in cache.listings() if l.get("Lat") and l.get("Status") != "Dead"
    ]
    bs = [
        {"id": key(b["id"]), "lat": b["Lat"], "lng": b["Lng"], "title": b.get("Address"), "units": b.get("Units"),
         "mgmt": b.get("Mgmt company"), "status": b.get("Outreach status"), "priority": b.get("Priority")}
        for b in cache.buildings() if b.get("Lat")
    ]
    return JSONResponse({"listings": ls, "buildings": bs})


# ------------------------------------------------------------------ buildings

@app.get("/buildings", response_class=HTMLResponse)
def buildings(request: Request, status: str = ""):
    bs = cache.buildings()
    if status:
        bs = [b for b in bs if b.get("Outreach status") == status]
    bs.sort(key=lambda b: -(b.get("Priority") or 0))
    companies_path = STATE_DIR / "mgmt_companies.json"
    companies = json.loads(companies_path.read_text())[:20] if companies_path.exists() else []
    return render(request, "buildings.html", buildings=bs, companies=companies, status=status, building_statuses=BUILDING_STATUSES)


@app.get("/b/{page_id}", response_class=HTMLResponse)
def building_detail(request: Request, page_id: str, saved: str = ""):
    b = cache.building(page_id) or cache.refresh_building(page_id)
    return render(request, "building.html", b=b, saved=saved, building_statuses=BUILDING_STATUSES)


@app.post("/b/{page_id}/generate")
def building_generate(page_id: str):
    from src.score import draft_building_outreach

    b = cache.building(page_id) or cache.refresh_building(page_id)
    d = draft_building_outreach(b)
    text = f"Subject: {d['subject']}\n\n{d['body']}\n\n--- Phone script ---\n{d['phone_script']}"
    store.set_building_fields(page_id, draft=text)
    cache.refresh_building(page_id)
    return RedirectResponse(f"/b/{page_id}", status_code=303)


@app.post("/b/{page_id}/save")
def building_save(page_id: str, draft: str = Form(""), notes: str = Form(""), status: str = Form(""),
                  contact_email: str = Form(""), contact_phone: str = Form(""), website: str = Form("")):
    fields = dict(draft=draft, notes=notes, contact_email=contact_email.strip(), contact_phone=contact_phone.strip(), website=website.strip())
    if status in BUILDING_STATUSES:
        fields["status"] = status
    store.set_building_fields(page_id, **fields)
    cache.refresh_building(page_id)
    return RedirectResponse(f"/b/{page_id}?saved=1", status_code=303)


@app.post("/b/{page_id}/gmail-draft")
def building_gmail_draft(page_id: str, draft: str = Form(...), to: str = Form("")):
    from src import gmail_out

    b = cache.building(page_id) or {}
    body = draft.split("--- Phone script ---")[0].strip()
    subject = f"Upcoming vacancies at {b.get('Address')}?"
    if body.lower().startswith("subject:"):
        first, _, rest = body.partition("\n")
        subject, body = first.split(":", 1)[1].strip(), rest.strip()
    draft_id = gmail_out.create_draft(subject, body, to=to.strip())
    store.set_building_fields(page_id, draft=draft, gmail_draft_id=draft_id, status="Drafted")
    cache.refresh_building(page_id)
    return RedirectResponse(f"/b/{page_id}?saved=draft", status_code=303)


# ------------------------------------------------------------------ activity

@app.get("/activity", response_class=HTMLResponse)
def activity(request: Request):
    repo = cfg()["github"]["repo"]
    runs = gh_cached("runs", lambda: json.loads(gh("run", "list", "-R", repo, "-L", "15", "--json",
                                                    "workflowName,status,conclusion,createdAt,url,event")))
    last = gh_cached("last_run", lambda: json.loads(gh("api", f"repos/{repo}/contents/state/last_run.json",
                                                        "-H", "Accept: application/vnd.github.raw")))
    notified = sorted((l for l in cache.listings() if l.get("Notified at")), key=lambda l: l["Notified at"], reverse=True)[:20]
    drafted = sorted((l for l in cache.listings() if l.get("Gmail draft ID")), key=lambda l: l.get("last_edited_time") or "", reverse=True)[:20]
    return render(request, "activity.html", runs=runs, last=last, notified=notified, drafted=drafted, repo=repo)


@app.post("/poll-now")
def poll_now():
    repo = cfg()["github"]["repo"]
    try:
        gh("workflow", "run", "poll.yml", "-R", repo)
        _gh_cache.pop("runs", None)
        msg = "Poll triggered"
    except Exception as e:
        msg = f"Could not trigger: {e}"
    return HTMLResponse(f'<span class="flash">{msg}</span>')


@app.post("/sync-now")
def sync_now():
    n = cache.pull()
    return HTMLResponse(f'<span class="flash">Synced {n} pages</span>')
