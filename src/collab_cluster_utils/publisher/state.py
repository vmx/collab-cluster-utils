"""SQLite-backed ledger of which datasets have already been notified to the
target node.

A plain point-lookup table stays cheap regardless of size, which is what
lets reconcile.py check it per item without that cost growing over time;
forget() keeps it roughly the size of what's currently on disk rather than
accumulating forever.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


class State:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notified (
                info_hash TEXT PRIMARY KEY,
                notified_at TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def is_notified(self, info_hash: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM notified WHERE info_hash = ?", (info_hash,)).fetchone()
        return row is not None

    def mark_notified(self, info_hash: str, notified_at: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO notified (info_hash, notified_at) VALUES (?, ?)",
            (info_hash, notified_at),
        )
        self._conn.commit()

    def forget(self, info_hash: str) -> None:
        self._conn.execute("DELETE FROM notified WHERE info_hash = ?", (info_hash,))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
