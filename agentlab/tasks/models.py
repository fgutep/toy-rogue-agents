"""Task-system data models (design doc 02 §2.1, §3, §4)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class TaskMeta(BaseModel):
    """``task.yaml`` metadata (doc 02 §2.1).

    ``solvable`` is the critical hidden field: read only by the orchestrator and
    the oracle, never surfaced to an agent or referenced in anything an agent can
    read. A broken task is otherwise indistinguishable in form from a solvable
    one.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    category: str
    solvable: bool
    difficulty: Literal["easy", "medium", "hard"] = "easy"
    est_solve_steps: int = Field(default=10, ge=1)
    brief_ref: str = "brief.md"
    target_build: str | None = "target/"
    score_cmd: str = "score.sh"


class TaskInstance(BaseModel):
    """A freshly instantiated task template (doc 02 §3).

    An instance binds a task template to a set of sampled ``params`` (flag
    content, port offsets, in-target constants) for one ``instance_tag``, so the
    same template can be handed to several agents — or several waves — without
    one agent's solution being directly copyable by another.
    """

    model_config = ConfigDict(extra="forbid")

    task_id: str
    instance_tag: str
    params: dict[str, Any] = Field(default_factory=dict)
    rendered_brief: str = ""

    @property
    def instance_id(self) -> str:
        return f"{self.task_id}@{self.instance_tag}"


class Manifest(BaseModel):
    """The assignment manifest (doc 02 §4), generated once per run/wave.

    Emitted as the first telemetry event of a run so ground truth about what was
    assigned is always available independent of anyone's notes.
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str
    seed: int
    generated_at: str
    # agent_id -> list of instance_ids assigned to that agent
    assignments: dict[str, list[str]] = Field(default_factory=dict)
    # instance_id -> the instantiated task it refers to
    instances: dict[str, TaskInstance] = Field(default_factory=dict)
