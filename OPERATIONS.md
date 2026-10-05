# Operations

## What runs where
| Piece | Where | Schedule |
|---|---|---|
| Poller (`src/run.py`) | GitHub Actions `poll.yml` | every 10 min (GitHub may delay a few minutes) |
| Digest (`src/digest.py`) | GitHub Actions `digest.yml` | ~8am / 6pm New York |
| Off-market map (`src/offmarket/build_map.py`) | GitHub Actions `offmarket.yml` | Mondays |
| Tracker UI | this Mac, launchd `com.apt-hunt.tracker` | always on → http://localhost:8765 |

Notion "Apt Hunt" (Listings + Buildings) is the source of truth. `state/` holds dedupe state, committed by the bot.

## Feeds
- Gmail label `apt-feed` (filters created by `scripts/setup_gmail.py`): StreetEasy, Zillow, RentHop, Apartments.com, Leasebreak, Listings Project, Facebook group notifications.
- Craigslist JSON search (direct).
- `mgmt_sites` in `config.yaml` (management-company listing pages; empty until populated).

## Budget rule
Asking rent ≤ `max_gross_price` ($5,500) and net effective ≤ `max_price` ($5,000). Claude extracts concessions and computes net effective; over-$5k listings without a qualifying concession are scored as rejects.

## When a run fails (GitHub emails you)
The poller exits non-zero only for problems that need a human. The log line starts with `ACTION NEEDED`:
- **Gmail login expired** (expected about every 7 days; the OAuth app is in Testing mode): run
  `uv run python scripts/gmail_auth.py`, click Allow, then `make secrets`.
- **Anthropic credit balance too low**: add credits in the Claude Platform console.
- **Notion token rejected**: re-share the Apt Hunt page with the `apt-hunt` integration.

## Common commands
```
make dry            # parse + score without writing anything
make poll           # one real poll from this Mac
make offmarket      # rebuild the building map
uv run python -m src.offmarket.drafts --top 20   # outreach drafts into Notion
make install-tracker / make uninstall-tracker
gh workflow run poll.yml -R whale-n/apt-hunt      # poll now in the cloud
```

## Cost guard
`scoring.max_per_run` (40) caps Claude calls per poll; extra listings wait for the next run.
