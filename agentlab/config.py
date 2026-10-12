"""Run / agent configuration and loading (design doc 01 §4, doc 02 §4).

Every run is fully specified by one YAML file plus a seed. These models are the
validated in-memory form of that file. ``model_config`` here is Pydantic's, not
to be confused with :attr:`AgentConfig.model_config_` which is the *backend's*
settings (we name it with a trailing underscore to dodge the Pydantic reserved
name).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

_ENV_PATTERN = re.compile(r"\$\{env:([A-Z0-9_]+)\}")


class AssignmentRules(BaseModel):
    """Parameters for the manifest generator (doc 02 §4). Logic is data, not code."""

    model_config = ConfigDict(extra="forbid")

    tasks_per_agent: int = Field(ge=1)
    broken_fraction: float = Field(ge=0.0, le=1.0, default=0.0)
    overlap_rate: float = Field(ge=0.0, le=1.0, default=0.0)
    # Explicit coverage constraints the sampler must *guarantee*, not hope for
    # (doc 02 §4). At small agent counts random sampling can't be trusted.
    every_agent_gets_broken: bool = True


class AgentConfig(BaseModel):
    """Per-agent slice of the run config (doc 01 §4)."""

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    agent_id: str
    model_backend: str  # registry key: "openai" | "luna" | "terra" | "codestral" | "mock"
    model_config_: dict[str, Any] = Field(default_factory=dict, alias="model_config")
    max_steps: int = Field(ge=1, default=40)
    max_tokens_per_turn: int = Field(ge=1, default=4096)
    # Optional overall transcript budget; the loop ends the agent when the
    # untruncated transcript would exceed it (doc 01 §3.2 — budgets are
    # step/token-based, never wall-clock, and never met by compaction).
    max_total_tokens: int | None = Field(default=None, ge=1)
    temperature: float = Field(ge=0.0, le=2.0, default=0.8)
    effort: str | None = None  # normalized "reasoning effort" knob (doc 01 §2.3)
    system_prompt_ref: str
    task_refs: list[str] = Field(default_factory=list)
    # Container hardening overrides must carry a logged justification (doc 01 §3.1).
    hardening_overrides: dict[str, str] = Field(default_factory=dict)

    @field_validator("agent_id")
    @classmethod
    def _safe_id(cls, v: str) -> str:
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", v):
            raise ValueError(f"unsafe agent_id {v!r}: use [a-zA-Z0-9_-], <=64 chars")
        return v


class NetworkConfig(BaseModel):
    """Egress allowlist the framework *mechanism* enforces (doc 01 §3.1)."""

    model_config = ConfigDict(extra="forbid")

    bridge_name: str = "agentlab-agents"
    egress_allowlist: list[str] = Field(default_factory=list)


class RunConfig(BaseModel):
    """The single top-level config that drives a whole run (doc 01 §4)."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    seed: int
    experiment_dir: str  # root of the experiment repo (tasks/, prompts/, ...)
    telemetry_url: str = "http://telemetry-sink:8900/events"
    agents: list[AgentConfig]
    assignment: AssignmentRules
    network: NetworkConfig = Field(default_factory=NetworkConfig)

    @field_validator("agents")
    @classmethod
    def _unique_ids(cls, v: list[AgentConfig]) -> list[AgentConfig]:
        ids = [a.agent_id for a in v]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate agent_id in agents list")
        if not ids:
            raise ValueError("at least one agent is required")
        return v

    def experiment_path(self) -> Path:
        return Path(self.experiment_dir)


def _expand_env(obj: Any) -> Any:
    """Recursively replace ``${env:NAME}`` markers with environment values.

    Secrets are read from the environment, never hardcoded (doc 01 §2.3). A
    missing variable is a hard error so a run never silently uses an empty key.
    """
    if isinstance(obj, str):
        def repl(m: re.Match[str]) -> str:
            name = m.group(1)
            try:
                return os.environ[name]
            except KeyError as e:
                raise KeyError(f"config references ${{env:{name}}} but it is unset") from e

        return _ENV_PATTERN.sub(repl, obj)
    if isinstance(obj, dict):
        return {k: _expand_env(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_env(v) for v in obj]
    return obj


def load_run_config(path: str | Path) -> RunConfig:
    """Load and validate a run config from YAML, expanding ``${env:...}``."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return RunConfig.model_validate(_expand_env(raw))
