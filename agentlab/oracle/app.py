"""Oracle service (design doc 04 Part 1).

Exposes exactly one agent-facing endpoint, ``POST /submit``, which the agents'
``submit`` tool calls through the egress allowlist. The response contains only
the agent-facing string (doc 04 §3); the full dual record is logged out-of-band
to the telemetry sink by the :class:`Grader`.

Agent identity is taken from the proxy-injected ``X-Agent-Identity`` header, not
from the request body, consistent with the Armory and honeypot (doc 03 §1.4).

The calibration trace check needs timeline provenance, which is a host-side
resource; when an event-store path is provided the service computes a minimal
provenance-based check, but it still exposes *only* ``/submit`` to the agent
side — it never serves the event store. The richer trace comparison is the
forensics pass's job (doc 03 §4 / doc 04 §4).

Run:
    uvicorn agentlab.oracle.app:app --host 0.0.0.0 --port 8600

Environment:
    AGENTLAB_MANIFEST   path to the run manifest JSON (for expected flags)
    AGENTLAB_TELEMETRY  telemetry sink events URL
"""

from __future__ import annotations

import json
import os

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse

from agentlab.oracle.grader import Grader
from agentlab.schema import Submission
from agentlab.tasks.models import Manifest
from agentlab.telemetry import TelemetryClient


def _load_calibration(manifest: Manifest) -> dict[str, str]:
    """Each agent's first assigned instance is its one calibration task (doc 04 §3.3)."""
    return {
        agent_id: iids[0]
        for agent_id, iids in manifest.assignments.items()
        if iids
    }


def create_app(grader: Grader | None = None) -> FastAPI:
    app = FastAPI(title="oracle", docs_url=None, redoc_url=None, openapi_url=None)

    if grader is None:
        manifest = Manifest.model_validate(
            json.loads(open(os.environ["AGENTLAB_MANIFEST"], encoding="utf-8").read())
        )
        telemetry = TelemetryClient(
            os.environ.get("AGENTLAB_TELEMETRY", "http://telemetry-sink:8900/events")
        )
        grader = Grader(
            instances=manifest.instances,
            telemetry=telemetry,
            calibration_instances=_load_calibration(manifest),
        )
    app.state.grader = grader

    @app.post("/submit")
    async def submit(request: Request) -> PlainTextResponse:
        body = await request.json()
        agent_id = request.headers.get("x-agent-identity", "unknown")
        submission = Submission.model_validate(body)
        result = app.state.grader.grade(agent_id, submission)
        # Only the agent-facing string ever crosses back to the agent side.
        return PlainTextResponse(result.agent_facing_response)

    return app


app = create_app()
