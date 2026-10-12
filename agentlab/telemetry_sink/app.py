"""Telemetry sink service — the one inbound bridge from the agent side (doc 01 §3.4).

This is the *only* component reachable from the agents' network that accepts
their telemetry, and it accepts **writes only**. It exposes nothing queryable:
even a compromised or extremely well-generalizing agent that somehow reached
this address finds a write endpoint, not a read one (doc 04 §3). All reads
happen on the host-only side, against the same :class:`EventStore`, through the
UI backend — a separate process on a separate interface.

The sink assigns ``ts`` and keeps ``event_id`` as a single authoritative clock,
eliminating container clock-skew (doc 03 §3.2).

Run (on the Ubuntu host, inside the agents' bridge network namespace):

    uvicorn agentlab.telemetry_sink.app:app --host 0.0.0.0 --port 8900

Environment:
    AGENTLAB_EVENT_DB   path to the run's SQLite event store
    AGENTLAB_BLOB_DIR   (optional) path to the content-addressed blob dir
"""

from __future__ import annotations

import os

from fastapi import FastAPI, Request, Response

from agentlab.eventstore import EventStore
from agentlab.telemetry import Event


def create_app(store: EventStore | None = None) -> FastAPI:
    app = FastAPI(title="agentlab telemetry sink", docs_url=None, redoc_url=None,
                  openapi_url=None)
    if store is None:
        db = os.environ.get("AGENTLAB_EVENT_DB", "./run-events.sqlite3")
        store = EventStore(db, os.environ.get("AGENTLAB_BLOB_DIR"))
    app.state.store = store

    @app.post("/events", status_code=204)
    async def ingest(request: Request) -> Response:
        # Parse defensively: a malformed event must never crash the sink or
        # reveal parsing detail back to the sender.
        try:
            body = await request.json()
            event = Event.model_validate(body)
            event.ts = None  # force sink-assigned timestamp
            app.state.store.append(event)
        except Exception:
            pass
        # Always 204, regardless of outcome: no delivery signal flows back.
        return Response(status_code=204)

    @app.put("/blobs")
    async def ingest_blob(request: Request) -> Response:
        # Full bodies (e.g. Armory PUT payloads) are stored content-addressed,
        # separately from the structured log line (doc 03 §1.4). Returns 204 and
        # no digest, keeping the endpoint write-only in spirit.
        try:
            data = await request.body()
            app.state.store.put_blob(data)
        except Exception:
            pass
        return Response(status_code=204)

    return app


app = create_app()
