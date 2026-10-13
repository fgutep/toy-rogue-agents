from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Backend = Literal["openai", "luna", "terra", "codestral", "mock"]


class ScenarioConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    agents: int = Field(ge=1, le=64)
    tasks_per_agent: int = Field(ge=1)
    broken_fraction: float = Field(ge=0.0, le=1.0)
    overlap_rate: float = Field(ge=0.0, le=1.0)
    plant_note: bool
    note_path: str | None = None
    # Optional override for the planted note's body. When null, the orchestrator
    # plants the default PLANTED_NOTE. Lets a scenario plant a different note
    # (e.g. one that lures agents toward the honeypot tripwire).
    note_content: str | None = None
    max_steps: int = Field(ge=1)
    model_backend: Backend
    seed: int

    @model_validator(mode="after")
    def note_is_consistent(self) -> ScenarioConfig:
        if self.plant_note and not self.note_path:
            raise ValueError("note_path is required when plant_note is true")
        if not self.plant_note and self.note_path is not None:
            raise ValueError("note_path must be null when plant_note is false")
        if not self.plant_note and self.note_content is not None:
            raise ValueError("note_content must be null when plant_note is false")
        return self


class WaveSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    wave_count: int = Field(ge=1)
    wave_interval_minutes: int = Field(ge=0)
    agents_per_wave: int = Field(ge=1, le=64)
    persist_armory_across_waves: bool
    persist_honeypot_across_waves: bool
    model_backend: Backend
    seed: int


class WaveManifestTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tasks_per_agent: int = Field(ge=1)
    broken_fraction: float = Field(ge=0.0, le=1.0)
    overlap_rate: float = Field(ge=0.0, le=1.0)
    plant_note: Literal[False]
    max_steps: int = Field(ge=1)
