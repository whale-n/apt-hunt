"""Notion <-> local SQLite cache. Notion is the source of truth; the cache makes the UI instant."""

import json
import logging
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.notion_store import NotionStore

log = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent / "cache.sqlite"
INTERVAL = 60


def key(page_id: str) -> str:
    return page_id.replace("-", "")


class Cache:
    def __init__(self, store: NotionStore):
        self.store = store
        self.lock = threading.Lock()
        self.db = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS listings (id TEXT PRIMARY KEY, data TEXT, edited TEXT);
            CREATE TABLE IF NOT EXISTS buildings (id TEXT PRIMARY KEY, data TEXT, edited TEXT);
            CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
            """
        )
        self.last_error: str | None = None

    # ---- reads ----
    def _all(self, table: str) -> list[dict]:
        with self.lock:
            rows = self.db.execute(f"SELECT data FROM {table}").fetchall()
        return [json.loads(r[0]) for r in rows]

    def listings(self) -> list[dict]:
        return self._all("listings")

    def buildings(self) -> list[dict]:
        return self._all("buildings")

    def listing(self, page_id: str) -> dict | None:
        with self.lock:
            row = self.db.execute("SELECT data FROM listings WHERE id=?", (key(page_id),)).fetchone()
        return json.loads(row[0]) if row else None

    def building(self, page_id: str) -> dict | None:
        with self.lock:
            row = self.db.execute("SELECT data FROM buildings WHERE id=?", (key(page_id),)).fetchone()
        return json.loads(row[0]) if row else None

    def meta(self, k: str) -> str | None:
        with self.lock:
            row = self.db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return row[0] if row else None

    # ---- writes ----
    def _put(self, table: str, item: dict) -> None:
        with self.lock:
            self.db.execute(
                f"INSERT OR REPLACE INTO {table} (id, data, edited) VALUES (?, ?, ?)",
                (key(item["id"]), json.dumps(item), item.get("last_edited_time")),
            )
            self.db.commit()

    def refresh_listing(self, page_id: str) -> dict:
        item = self.store.flatten(self.store.get(page_id))
        self._put("listings", item)
        return item

    def refresh_building(self, page_id: str) -> dict:
        item = self.store.flatten(self.store.get(page_id))
        self._put("buildings", item)
        return item

    def pull(self) -> int:
        since = self.meta("last_pull")
        # Notion's last_edited_time has minute granularity; overlap the window.
        since_q = (datetime.fromisoformat(since) - timedelta(minutes=3)).isoformat() if since else None
        started = datetime.now(timezone.utc).isoformat()
        n = 0
        for item in self.store.listings_since(since_q):
            self._put("listings", item)
            n += 1
        for item in self.store.buildings_since(since_q):
            self._put("buildings", item)
            n += 1
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO meta (k, v) VALUES ('last_pull', ?)", (started,))
            self.db.commit()
        return n

    def start(self) -> None:
        def loop():
            while True:
                try:
                    n = self.pull()
                    self.last_error = None
                    if n:
                        log.info("synced %d pages from Notion", n)
                except Exception as e:  # keep serving the cache when Notion hiccups
                    self.last_error = str(e)
                    log.warning("Notion sync failed: %s", e)
                time.sleep(INTERVAL)

        threading.Thread(target=loop, daemon=True, name="notion-sync").start()
