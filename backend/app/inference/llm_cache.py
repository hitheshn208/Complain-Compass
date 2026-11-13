"""Persistent cache of OpenAI responses, kept in its own SQLite file.

It lives outside ``storage/`` on purpose. Deleting ``storage/`` resets the application
(complaints, issues, vectors), but the paid LLM responses survive, so re-uploading the same
data after a reset makes (almost) no API calls. Keys are content hashes (see
``LLMClassifier._key``), never database IDs, so they stay valid across resets.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path

from app.database.models import utcnow_iso

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_cache (
    cache_key   TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    model       TEXT NOT NULL,
    response    TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
"""


class LLMCache:
    """Thread-safe key/value store with the same ``cache_get`` / ``cache_put`` interface as Repository."""

    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def cache_get(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT response FROM llm_cache WHERE cache_key = ?", (key,)).fetchone()
        return row[0] if row else None

    def cache_put(self, key: str, kind: str, model: str, response: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO llm_cache (cache_key, kind, model, response, created_at) VALUES (?, ?, ?, ?, ?)",
                (key, kind, model, response, utcnow_iso()),
            )
            self._conn.commit()

    def count(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM llm_cache").fetchone()[0])

    def import_from(self, conn: sqlite3.Connection) -> int:
        """Move rows from the legacy ``llm_cache`` table in the main database into this file
        (older versions stored the cache there). Existing keys here win. Returns rows moved."""
        rows = conn.execute("SELECT cache_key, kind, model, response, created_at FROM llm_cache").fetchall()
        if not rows:
            return 0
        with self._lock:
            self._conn.executemany(
                "INSERT OR IGNORE INTO llm_cache (cache_key, kind, model, response, created_at) VALUES (?, ?, ?, ?, ?)",
                [tuple(r) for r in rows],
            )
            self._conn.commit()
        conn.execute("DELETE FROM llm_cache")
        conn.commit()
        return len(rows)

    def close(self) -> None:
        with self._lock:
            self._conn.close()
