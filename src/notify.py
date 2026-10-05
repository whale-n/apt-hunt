"""Compose notification emails (hot alerts and the twice-daily digest)."""

from html import escape

from src.config import cfg


def _tracker_link(page_id: str) -> str:
    return f"{cfg()['notify']['tracker_base_url']}/l/{page_id.replace('-', '')}"


def _card(item: dict) -> str:
    photo = item.get("photo")
    img = f'<img src="{escape(photo)}" width="560" style="max-width:100%;border-radius:8px;display:block;margin-bottom:8px">' if photo else ""
    reasons = "<br>".join(escape(r.lstrip("- ")) for r in (item.get("reasons") or "").splitlines() if r.strip())
    return f"""
<div style="border:1px solid #ddd;border-radius:10px;padding:14px;margin:0 0 16px;font-family:-apple-system,Helvetica,Arial,sans-serif">
  {img}
  <div style="font-size:17px;font-weight:600;margin-bottom:4px">{escape(item['headline'])}</div>
  <div style="color:#555;font-size:13px;margin-bottom:8px">{escape(item.get('sub', ''))}</div>
  <div style="font-size:14px;line-height:1.45;margin-bottom:10px">{reasons}</div>
  <a href="{escape(item.get('url') or '#')}" style="margin-right:14px">Open listing</a>
  <a href="{escape(_tracker_link(item['page_id']))}" style="margin-right:14px">Open in tracker</a>
  <a href="{escape(item.get('notion_url') or '#')}">Notion</a>
</div>"""


def listing_item(page_id: str, notion_url: str | None, listing, score: dict) -> dict:
    beds = (listing.beds or "?").upper().replace("STUDIO", "Studio")
    price = f"${listing.price:,}" if listing.price else "$?"
    where = listing.address.split(",")[0] if listing.address else listing.title[:50]
    walk = f"{listing.walk_min:.0f} min walk" if listing.walk_min is not None else "walk ?"
    tags = ", ".join(score.get("tags", []))
    return {
        "page_id": page_id,
        "notion_url": notion_url,
        "url": listing.url,
        "photo": listing.photos[0] if listing.photos else None,
        "score": score["score"],
        "headline": f"{beds} {price} · {where} · {walk}",
        "sub": f"Score {score['score']} · {listing.source}" + (f" · {tags}" if tags else ""),
        "reasons": score.get("reasons", ""),
        "draft": score.get("draft", ""),
    }


def hot_email(item: dict) -> tuple[str, str, str]:
    subject = f"[APT {item['score']}] {item['headline']}"
    draft = escape(item.get("draft") or "").replace("\n", "<br>")
    html = _card(item) + (
        f'<div style="font-family:-apple-system,Helvetica,Arial,sans-serif;font-size:13px;color:#333">'
        f"<b>Outreach draft</b> (saved to your Gmail drafts):<br><br>{draft}</div>" if draft else ""
    )
    text = f"{item['headline']}\n{item.get('url')}\n\n{item.get('reasons')}"
    return subject, html, text


def digest_email(items: list[dict]) -> tuple[str, str, str]:
    subject = f"[APT digest] {len(items)} good listing{'s' if len(items) != 1 else ''}"
    html = "".join(_card(i) for i in sorted(items, key=lambda i: -i["score"]))
    text = "\n\n".join(f"{i['headline']}\n{i.get('url')}" for i in items)
    return subject, html, text
