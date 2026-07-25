from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class Manifest:
    """SQLite download ledger so re-runs are idempotent and auditable."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS artifacts (
                source TEXT NOT NULL,
                key TEXT NOT NULL,
                url TEXT,
                path TEXT,
                bytes INTEGER,
                sha256 TEXT,
                status TEXT,
                fetched_at TEXT,
                meta_json TEXT,
                PRIMARY KEY (source, key)
            )
            """
        )
        self.conn.commit()

    def upsert(self, source: str, key: str, record: dict[str, Any]) -> None:
        import orjson

        with self._lock:
            self.conn.execute(
                """
                INSERT INTO artifacts(source, key, url, path, bytes, sha256, status, fetched_at, meta_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, key) DO UPDATE SET
                    url=excluded.url,
                    path=excluded.path,
                    bytes=excluded.bytes,
                    sha256=excluded.sha256,
                    status=excluded.status,
                    fetched_at=excluded.fetched_at,
                    meta_json=excluded.meta_json
                """,
                (
                    source,
                    key,
                    record.get("url"),
                    record.get("path"),
                    record.get("bytes"),
                    record.get("sha256"),
                    record.get("status"),
                    datetime.now(timezone.utc).isoformat(),
                    orjson.dumps(record).decode(),
                ),
            )
            self.conn.commit()

    def count(self, source: str | None = None) -> int:
        with self._lock:
            if source:
                row = self.conn.execute(
                    "SELECT COUNT(*) AS c FROM artifacts WHERE source=?", (source,)
                ).fetchone()
            else:
                row = self.conn.execute("SELECT COUNT(*) AS c FROM artifacts").fetchone()
            return int(row["c"])

    def close(self) -> None:
        with self._lock:
            self.conn.close()
