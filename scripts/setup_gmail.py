"""Create the Gmail labels and the filter that routes listing alerts into the feed label.

    uv run python scripts/setup_gmail.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.config import cfg  # noqa: E402
from src.google_auth import gmail  # noqa: E402
from src.ingest.gmail import label_id  # noqa: E402


def main() -> None:
    g = cfg()["gmail"]
    feed = label_id(g["feed_label"])
    label_id(g["processed_label"])
    label_id(g["hot_label"])

    domains = [d for source, ds in g["feed_senders"].items() if source != "facebook" for d in ds]
    queries = [
        "from:(" + " OR ".join(domains) + ")",
        'from:facebookmail.com ("posted in" OR "new post in" OR "Marketplace")',
    ]
    existing = gmail().users().settings().filters().list(userId="me").execute().get("filter", [])
    existing_queries = {f.get("criteria", {}).get("query") for f in existing}
    for q in queries:
        if q in existing_queries:
            print(f"filter exists: {q}")
            continue
        gmail().users().settings().filters().create(
            userId="me", body={"criteria": {"query": q}, "action": {"addLabelIds": [feed]}}
        ).execute()
        print(f"created filter: {q}")
    print("Labels and filters ready.")


if __name__ == "__main__":
    main()
