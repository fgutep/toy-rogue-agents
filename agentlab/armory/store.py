"""Armory object store — append-only versioning behind an overwrite-looking API.

To an agent the Armory looks like ordinary overwrite-capable storage. Internally
every version of every object is retained (doc 03 §1.4), so an agent
"deleting" or overwriting something is a recoverable, loggable event, not a loss
of ground truth. This persistence is also what lets the same Armory survive
across waves of a longitudinal run (doc 03 §6 / doc 02 §6).
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS objects (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    path        TEXT NOT NULL,
    body_sha256 TEXT NOT NULL,
    body        BLOB NOT NULL,
    body_size   INTEGER NOT NULL,
    writer      TEXT NOT NULL,
    ts          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_objects_path ON objects(path);
"""


@dataclass
class ObjectVersion:
    path: str
    body: bytes
    body_sha256: str
    body_size: int
    writer: str
    ts: str


class ArmoryStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def put(self, path: str, body: bytes, writer: str) -> str:
        sha = hashlib.sha256(body).hexdigest()
        ts = _dt.datetime.now(_dt.UTC).isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT INTO objects (path, body_sha256, body, body_size, writer, ts) "
                "VALUES (?,?,?,?,?,?)",
                (path, sha, body, len(body), writer, ts),
            )
            self._conn.commit()
        return sha

    def get_latest(self, path: str) -> ObjectVersion | None:
        cur = self._conn.execute(
            "SELECT path, body, body_sha256, body_size, writer, ts FROM objects "
            "WHERE path = ? ORDER BY seq DESC LIMIT 1",
            (path,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return ObjectVersion(*row)

    def list_prefix(self, prefix: str) -> list[str]:
        """Distinct object paths under a prefix (latest-version view)."""
        cur = self._conn.execute(
            "SELECT DISTINCT path FROM objects WHERE path LIKE ? ORDER BY path",
            (prefix + "%",),
        )
        return [r[0] for r in cur.fetchall()]

    def close(self) -> None:
        self._conn.close()
