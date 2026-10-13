"""Append-only event store + content-addressed blob store (design doc 03 §3, §1.4).

One SQLite file per run holds the normalized event envelopes; a sibling
directory holds full request/response bodies, content-addressed by SHA-256 so
large objects never bloat the event stream but are always retrievable for
forensic reconstruction.

The store is **append-only**: there is an ``append`` and there are reads, but no
update or delete. Even an agent "overwriting" an Armory object is recorded as a
new event over a retained prior body, never a loss of ground truth (doc 03
§1.4). The store assigns ``ts`` and ``event_id`` authoritatively on append, so
container clock skew cannot corrupt ordering (doc 03 §3.2).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path

from agentlab.telemetry import Event, TelemetryClient

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id   TEXT NOT NULL UNIQUE,
    ts         TEXT NOT NULL,
    source     TEXT NOT NULL,
    kind       TEXT NOT NULL,
    payload    TEXT NOT NULL,
    refs       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_source ON events(source);
CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind);
"""


class EventStore:
    def __init__(self, db_path: str | Path, blob_dir: str | Path | None = None) -> None:
        self.db_path = Path(db_path)
        if not self.db_path.parent.exists():
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.blob_dir = Path(blob_dir) if blob_dir else self.db_path.parent / "blobs"
        if not self.blob_dir.exists():
            self.blob_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # -- events ----------------------------------------------------------- #
    def append(self, event: Event) -> Event:
        """Stamp ``ts`` (if absent) and persist. Returns the stored event."""
        stored = event.model_copy(deep=True)
        if stored.ts is None:
            stored.ts = Event.now_iso()
        with self._lock:
            self._conn.execute(
                "INSERT INTO events (event_id, ts, source, kind, payload, refs) "
                "VALUES (?,?,?,?,?,?)",
                (
                    stored.event_id, stored.ts, stored.source, stored.kind,
                    json.dumps(stored.payload), json.dumps(stored.refs),
                ),
            )
            self._conn.commit()
        return stored

    def _row_to_event(self, row: tuple) -> Event:
        _seq, event_id, ts, source, kind, payload, refs = row
        return Event(
            event_id=event_id, ts=ts, source=source, kind=kind,
            payload=json.loads(payload), refs=json.loads(refs),
        )

    def all_events(self) -> list[Event]:
        """All events in stable insertion order (the single authoritative clock)."""
        cur = self._conn.execute(
            "SELECT seq, event_id, ts, source, kind, payload, refs FROM events ORDER BY seq"
        )
        return [self._row_to_event(r) for r in cur.fetchall()]

    def query(self, *, source: str | None = None, kind: str | None = None,
              since_seq: int = 0) -> list[Event]:
        clauses, params = ["seq > ?"], [since_seq]
        if source is not None:
            clauses.append("source = ?")
            params.append(source)
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        sql = ("SELECT seq, event_id, ts, source, kind, payload, refs FROM events "
               f"WHERE {' AND '.join(clauses)} ORDER BY seq")
        cur = self._conn.execute(sql, params)
        return [self._row_to_event(r) for r in cur.fetchall()]

    def max_seq(self) -> int:
        cur = self._conn.execute("SELECT COALESCE(MAX(seq), 0) FROM events")
        return int(cur.fetchone()[0])

    # -- blobs ------------------------------------------------------------ #
    def put_blob(self, data: bytes) -> str:
        """Store ``data`` content-addressed; returns its SHA-256 hex digest."""
        digest = hashlib.sha256(data).hexdigest()
        path = self.blob_dir / digest
        if not path.exists():  # append-only: never rewrite an existing body
            path.write_bytes(data)
        return digest

    def get_blob(self, digest: str) -> bytes | None:
        path = self.blob_dir / digest
        return path.read_bytes() if path.exists() else None

    def close(self) -> None:
        self._conn.close()


class StoreTelemetryClient(TelemetryClient):
    """A telemetry client that appends directly to an :class:`EventStore`.

    For local, single-process dry-runs (the runnable sample, tests) where
    standing up the HTTP sink would add nothing. It preserves the outbound-only
    contract — :meth:`emit` still returns ``None`` and exposes no read path to
    the caller — while producing a real event store the timeline tool and UI can
    consume. It is **not** for use inside agent containers, where the real HTTP
    sink is the only permitted channel out (doc 01 §3.4).
    """

    def __init__(self, store: EventStore) -> None:
        self._store = store

    def emit(self, source, kind, payload, refs=None) -> None:
        self._store.append(Event(source=source, kind=kind, payload=payload, refs=refs or []))
        return None
