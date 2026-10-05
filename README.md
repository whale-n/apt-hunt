# apt-hunt

Personal apartment-search pipeline for north Williamsburg (≤10 min walk to the Bedford Av L).

- **Ingest:** listing-alert emails (StreetEasy, Zillow, RentHop, Apartments.com, Leasebreak, Listings Project, Facebook groups) from a Gmail label, plus Craigslist and management-company pages.
- **Filter + score:** walk time via OSRM, then Claude scores fit (ceilings, light, space, W/D, outdoor, fee) from text and photos and drafts an inquiry.
- **Track:** Notion is the source of truth; `make ui` runs a local tracker (FastAPI + HTMX) at http://localhost:8765.
- **Notify:** hot listings are emailed to the owner immediately; good ones go into an 8am/6pm digest. Outreach is always saved as Gmail drafts, never sent automatically.
- **Off-market:** `make offmarket` maps every multifamily building within the walk radius (NYC PLUTO + HPD registrations) with owner and manager contacts.

Runs on GitHub Actions every 10 minutes. Personal details and credentials live only in GitHub Secrets / a local `.env`.

See [PRIVACY.md](PRIVACY.md).
