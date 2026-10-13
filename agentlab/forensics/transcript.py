from __future__ import annotations

from agentlab.eventstore import EventStore
from agentlab.forensics.models import TimelineSlice, Transcript
from agentlab.reconstruct_timeline import reconstruct


def agent_ids(store: EventStore) -> list[str]:
    ids = {
        event.source.split(":", 1)[1]
        for event in store.all_events()
        if event.source.startswith("agent:")
    }
    return sorted(ids)


def build_transcript(store: EventStore, agent_id: str) -> Transcript:
    return [
        {"kind": event.kind, "payload": event.payload, "ts": event.ts}
        for event in store.query(source=f"agent:{agent_id}")
    ]


def build_timeline_slice(store: EventStore, agent_id: str) -> TimelineSlice:
    rows = []
    for row in reconstruct(store).merged_log:
        source = row.get("source", "")
        payload = row.get("payload", {})
        if source == f"agent:{agent_id}" or payload.get("agent_id") == agent_id:
            rows.append(row)
    return rows
