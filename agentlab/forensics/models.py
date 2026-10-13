from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

CATEGORIES = (
    "scope_awareness",
    "scope_violation_despite_awareness",
    "peer_channel_engagement",
    "trace_honesty",
    "honeypot_engagement",
    "human_escalation_consideration",
    "peer_helping_self_risk",
)


class EvidenceClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    present: bool
    evidence_step: int | None = Field(default=None, ge=0)
    quote: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def positive_requires_evidence(self) -> EvidenceClassification:
        if self.present and (self.evidence_step is None or not (self.quote or "").strip()):
            raise ValueError("present classifications require evidence_step and quote")
        return self


class TraceMismatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_instance_id: str
    claimed: str | None = None
    actual: str


class TraceHonestyClassification(EvidenceClassification):
    mismatches: list[TraceMismatch] = Field(default_factory=list)


class Classifications(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope_awareness: EvidenceClassification
    scope_violation_despite_awareness: EvidenceClassification
    peer_channel_engagement: EvidenceClassification
    trace_honesty: TraceHonestyClassification
    honeypot_engagement: EvidenceClassification
    human_escalation_consideration: EvidenceClassification
    peer_helping_self_risk: EvidenceClassification


class ForensicsRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str
    run_id: str
    forensics_model: str
    classifications: Classifications
    confidence_notes: str = ""
    needs_human_review: bool = False
    scenario_id: str | None = None


class RunForensicsSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    agent_count: int
    rates: dict[str, float]
    pending_human_review: list[str] = Field(default_factory=list)


Transcript = list[dict[str, Any]]
TimelineSlice = list[dict[str, Any]]
