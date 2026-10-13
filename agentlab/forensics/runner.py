from __future__ import annotations

from agentlab.adapters.base import ModelAdapter
from agentlab.eventstore import EventStore, StoreTelemetryClient
from agentlab.forensics.classifier import classify_transcript
from agentlab.forensics.models import CATEGORIES, ForensicsRecord, RunForensicsSummary
from agentlab.forensics.transcript import agent_ids, build_timeline_slice, build_transcript


def studied_families(store: EventStore) -> set[str]:
    families = {
        event.payload.get("backend")
        for event in store.query(kind="agent_start")
        if event.payload.get("backend")
    }
    if families:
        return families
    for event in store.query(kind="run_config"):
        families.update(
            agent.get("model_backend")
            for agent in event.payload.get("agents", [])
            if agent.get("model_backend")
        )
    return families


def ensure_independent_family(store: EventStore, forensics_family: str) -> None:
    studied = studied_families(store)
    if forensics_family in studied:
        raise ValueError(
            f"forensics family {forensics_family!r} matches a studied family: {sorted(studied)}"
        )


def aggregate(run_id: str, records: list[ForensicsRecord]) -> RunForensicsSummary:
    count = len(records)
    rates = {}
    for category in CATEGORIES:
        positive = sum(
            bool(getattr(record.classifications, category).present)
            for record in records
        )
        rates[category] = positive / count if count else 0.0
    return RunForensicsSummary(
        run_id=run_id,
        agent_count=count,
        rates=rates,
        pending_human_review=[r.agent_id for r in records if r.needs_human_review],
    )


def run_forensics(
    store: EventStore,
    *,
    run_id: str,
    adapter: ModelAdapter,
    forensics_model: str,
    scenario_id: str | None = None,
) -> tuple[list[ForensicsRecord], RunForensicsSummary]:
    ensure_independent_family(store, adapter.provider)
    telemetry = StoreTelemetryClient(store)
    records = []
    for agent_id in agent_ids(store):
        record, classifier_transcript = classify_transcript(
            build_transcript(store, agent_id),
            build_timeline_slice(store, agent_id),
            adapter,
            agent_id=agent_id,
            run_id=run_id,
            forensics_model=forensics_model,
        )
        record.scenario_id = scenario_id  # attached only after blinded classification
        telemetry.emit(
            source="forensics", kind="forensics_model_transcript",
            payload={
                "agent_id": agent_id,
                "messages": [message.model_dump() for message in classifier_transcript],
            },
        )
        telemetry.emit(
            source="forensics", kind="forensics_record", payload=record.model_dump()
        )
        records.append(record)
    summary = aggregate(run_id, records)
    telemetry.emit(source="forensics", kind="forensics_summary", payload=summary.model_dump())
    return records, summary
