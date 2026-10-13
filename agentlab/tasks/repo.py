"""Task repo loading, instantiation, and brief rendering (design doc 02 §2, §3, §5).

An agent never reads ``task.yaml`` or the repo directly. The orchestrator loads
templates here, instantiates them (sampling fresh params), renders the brief,
and appends only the rendered brief to the agent's initial messages (doc 02 §5).
Nothing about the repo's internal metadata — least of all the hidden
``solvable`` field — can leak to an agent through this path.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from string import Template

import yaml

from agentlab.tasks.models import TaskInstance, TaskMeta
from agentlab.tasks.params import sample_params

#: Param names that must never be substituted into an agent-facing brief. The
#: flag is the canonical hidden answer; it is discovered at runtime, not told.
_BRIEF_FORBIDDEN_PARAMS = {"flag"}


class TaskRepo:
    """A loaded view of a ``tasks/`` directory (doc 02 §2)."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        if not self.root.is_dir():
            raise FileNotFoundError(f"task repo not found: {self.root}")

    def task_ids(self) -> list[str]:
        return sorted(
            p.name for p in self.root.iterdir()
            if p.is_dir() and (p / "task.yaml").exists()
        )

    def load_meta(self, task_id: str) -> TaskMeta:
        meta_path = self.root / task_id / "task.yaml"
        raw = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
        meta = TaskMeta.model_validate(raw)
        if meta.id != task_id:
            raise ValueError(f"task.yaml id {meta.id!r} != directory {task_id!r}")
        return meta

    def ids_by_solvable(self, solvable: bool) -> list[str]:
        return [tid for tid in self.task_ids() if self.load_meta(tid).solvable is solvable]

    def _render_brief(self, task_id: str, meta: TaskMeta, params: dict) -> str:
        brief_path = self.root / task_id / meta.brief_ref
        template = brief_path.read_text(encoding="utf-8")
        safe = {k: v for k, v in params.items() if k not in _BRIEF_FORBIDDEN_PARAMS}
        # safe_substitute leaves unknown ${...} intact rather than raising, so a
        # brief that references a forbidden param simply shows the literal marker
        # instead of leaking the value.
        return Template(template).safe_substitute(safe)

    def instantiate(self, task_id: str, *, seed: int, instance_tag: str) -> TaskInstance:
        """Produce a fresh :class:`TaskInstance` (doc 02 §3).

        Container building for the target service is the execution backend's job
        (see :mod:`agentlab.orchestrator`); this returns the instance's params
        and rendered brief, which is all the task layer owns.
        """
        meta = self.load_meta(task_id)
        params = sample_params(
            self.root / task_id / "params.schema.yaml",
            seed=seed, task_id=task_id, instance_tag=instance_tag,
        )
        # Target coordinates are public instance parameters. The hostname is
        # deterministic and collision-resistant while remaining a valid Compose
        # service/DNS label.
        digest = hashlib.sha256(f"{task_id}|{instance_tag}".encode()).hexdigest()[:12]
        params.update({
            "task_id": task_id,
            "target_host": f"target-{digest}",
            "target_port": 8080,
        })
        brief = self._render_brief(task_id, meta, params)
        return TaskInstance(
            task_id=task_id, instance_tag=instance_tag,
            params=params, rendered_brief=brief, calibration=meta.calibration,
        )

    def score_cmd_path(self, task_id: str) -> Path:
        return self.root / task_id / self.load_meta(task_id).score_cmd
