"""SQLite-backed ledger of which .torrent files have already been notified to
the target node, keyed by file name so new ones are found without opening
anything. Durable so that a restart doesn't re-offer everything still on
disk -- including datasets someone has since removed from the node on
purpose.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path


class State:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notified (
                name TEXT PRIMARY KEY,
                info_hash TEXT NOT NULL,
                notified_at TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def names(self) -> set[str]:
        return {row[0] for row in self._conn.execute("SELECT name FROM notified")}

    def mark_notified(self, name: str, info_hash: str, notified_at: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO notified (name, info_hash, notified_at) VALUES (?, ?, ?)",
            (name, info_hash, notified_at),
        )
        self._conn.commit()

    def forget(self, names: Iterable[str]) -> None:
        self._conn.executemany("DELETE FROM notified WHERE name = ?", ((n,) for n in names))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
