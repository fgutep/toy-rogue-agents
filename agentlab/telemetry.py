"""The telemetry contract (design doc 01 §3.4) and unified event envelope (doc 03 §3.2).

``telemetry.emit(...)`` is the *only* way anything inside a container reaches the
outside world beyond its allowlisted tools. It is:

* outbound-only — the client has no read method at all, by construction;
* fire-and-forget — :meth:`TelemetryClient.emit` returns ``None`` and never
  exposes delivery, acknowledgement, or downstream processing to the caller,
  so a well-generalizing agent cannot learn anything about its own logging from
  the act of being logged.

Timestamps and ``event_id`` are assigned authoritatively *at the sink* on
receipt, not here, which eliminates the whole class of container clock-skew
problems the original investigators had to work around (doc 03 §3.2).
"""

from __future__ import annotations

import datetime as _dt
import uuid
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

Source = str  # "armory" | "honeypot" | "agent:{id}" | "orchestrator" | "operator" | "oracle"


class Event(BaseModel):
    """The common envelope every source normalizes into before storage (doc 03 §3.2)."""

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    ts: str | None = None  # ISO8601, assigned at the sink; None on the wire
    source: Source
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)
    # event_ids this event plausibly responds to; populated where derivable
    # (doc 03 §3.2). Ambiguous/cross-agent links are left for the forensics pass.
    refs: list[str] = Field(default_factory=list)

    @staticmethod
    def now_iso() -> str:
        return _dt.datetime.now(_dt.UTC).isoformat()


class TelemetryClient:
    """Outbound-only emitter. Deliberately has no ``read``/``query`` method.

    From inside a container this is the agent's only channel out besides its
    allowlisted tools, and it must never become a side channel the agent can
    observe. Errors are swallowed on purpose: delivery success or failure must
    not be inferable by the caller.
    """

    def __init__(self, url: str, *, timeout: float = 2.0) -> None:
        self._url = url
        self._timeout = timeout

    def emit(self, source: Source, kind: str, payload: dict[str, Any],
             refs: list[str] | None = None) -> None:
        event = Event(source=source, kind=kind, payload=payload, refs=refs or [])
        try:
            httpx.post(
                self._url,
                content=event.model_dump_json(exclude_none=True),
                headers={"content-type": "application/json"},
                timeout=self._timeout,
            )
        except Exception:
            # Fire-and-forget: the caller learns nothing about delivery.
            # (A dropped event is a sink-side availability concern, handled by
            # running the sink reliably — never by reporting back to the agent.)
            return None
        return None


class NullTelemetryClient(TelemetryClient):
    """A client that records events in-process. For tests and local dry-runs only.

    Preserves the outbound-only contract — callers still get ``None`` from
    :meth:`emit`; the captured list is for the *harness*, never reachable from
    within agent code paths.
    """

    def __init__(self) -> None:  # noqa: D401 - intentionally no network
        self.captured: list[Event] = []

    def emit(self, source: Source, kind: str, payload: dict[str, Any],
             refs: list[str] | None = None) -> None:
        self.captured.append(
            Event(source=source, kind=kind, payload=payload, refs=refs or [],
                  ts=Event.now_iso())
        )
        return None
