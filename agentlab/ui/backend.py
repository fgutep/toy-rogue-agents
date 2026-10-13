"""UI backend (design doc 04 Part 2).

Read-only over the event store, plus a small set of explicitly-logged operator
actions. It lives on a host-only interface that the agent containers have **no
route to** (doc 04 §2): bind it to ``127.0.0.1`` only. The agents' sole bridge
to this side is the write-only telemetry sink; nothing here is reachable from an
agent container.

What it serves (doc 04 §4):

* ``GET /api/events`` — incremental event feed (``since_seq`` cursor).
* ``GET /api/timeline`` — reconstructed timeline / provenance / influence graph.
* ``GET /api/manifest`` — the run manifest + config panel data.
* ``WS  /ws`` — websocket push of new events; the SPA falls back to polling.
* ``POST /api/operator/action`` — pause / resume / kill, each written to the
  event store as a first-class ``source: "operator"`` event so human
  intervention is part of the same reconstructable timeline, never a side
  channel (doc 04 §5). Enforcement (container pause/teardown) is performed by
  the orchestrator, which watches for these operator events.

Run (host-only!):
    uvicorn agentlab.ui.backend:app --host 127.0.0.1 --port 8500
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agentlab.eventstore import EventStore
from agentlab.reconstruct_timeline import reconstruct, timeline_to_dict
from agentlab.telemetry import Event

_STATIC = Path(__file__).parent / "static"


class HumanReview(BaseModel):
    agent_id: str
    decision: str  # "confirmed" | "rejected"
    reviewer: str = "operator"
    note: str | None = None


class OperatorAction(BaseModel):
    action: str  # "pause" | "resume" | "kill"
    scope: str  # "run" | "agent"
    agent_id: str | None = None
    operator: str = "operator"
    note: str | None = None


def create_app(store: EventStore | None = None, manifest_path: str | None = None) -> FastAPI:
    app = FastAPI(title="agentlab operator UI", docs_url=None, redoc_url=None)
    app.state.store = store or EventStore(
        os.environ.get("AGENTLAB_EVENT_DB", "./run-events.sqlite3")
    )
    app.state.manifest_path = manifest_path or os.environ.get("AGENTLAB_MANIFEST")

    @app.get("/api/events")
    def events(since_seq: int = 0) -> JSONResponse:
        evs = app.state.store.query(since_seq=since_seq)
        return JSONResponse({
            "max_seq": app.state.store.max_seq(),
            "events": [e.model_dump() for e in evs],
        })

    @app.get("/api/timeline")
    def timeline() -> JSONResponse:
        return JSONResponse(timeline_to_dict(reconstruct(app.state.store)))

    @app.get("/api/manifest")
    def manifest() -> JSONResponse:
        if app.state.manifest_path and Path(app.state.manifest_path).exists():
            return JSONResponse(json.loads(Path(app.state.manifest_path).read_text("utf-8")))
        return JSONResponse({"detail": "no manifest configured"}, status_code=404)

    @app.post("/api/operator/action")
    def operator_action(action: OperatorAction) -> JSONResponse:
        # Logged as a first-class event; the orchestrator enforces it (doc 04 §5).
        stored = app.state.store.append(Event(
            source="operator",
            kind=f"operator_{action.action}",
            payload=action.model_dump(),
        ))
        return JSONResponse({"ok": True, "event_id": stored.event_id})

    @app.get("/api/forensics/review-queue")
    def review_queue() -> JSONResponse:
        reviewed = {
            event.payload.get("agent_id")
            for event in app.state.store.query(source="human_review", kind="forensics_review")
        }
        pending = [
            event.payload
            for event in app.state.store.query(source="forensics", kind="forensics_record")
            if event.payload.get("needs_human_review")
            and event.payload.get("agent_id") not in reviewed
        ]
        return JSONResponse({"pending": pending})

    @app.post("/api/forensics/review")
    def forensics_review(review: HumanReview) -> JSONResponse:
        if review.decision not in {"confirmed", "rejected"}:
            return JSONResponse(
                {"detail": "decision must be confirmed or rejected"}, status_code=422
            )
        stored = app.state.store.append(Event(
            source="human_review", kind="forensics_review", payload=review.model_dump()
        ))
        return JSONResponse({"ok": True, "event_id": stored.event_id})

    @app.websocket("/ws")
    async def ws(socket: WebSocket) -> None:
        await socket.accept()
        cursor = 0
        try:
            while True:
                evs = app.state.store.query(since_seq=cursor)
                if evs:
                    cursor = app.state.store.max_seq()
                    await socket.send_text(json.dumps(
                        {"events": [e.model_dump() for e in evs], "max_seq": cursor}
                    ))
                await asyncio.sleep(0.5)  # push cadence; SPA also polls as fallback
        except WebSocketDisconnect:
            return

    if _STATIC.is_dir():
        app.mount("/", StaticFiles(directory=str(_STATIC), html=True), name="static")

    return app


app = create_app()
