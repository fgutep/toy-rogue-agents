"""Deterministic assignment-manifest generation (design doc 02 §4).

Assignment of tasks to agents is config-driven and reproducible, never left to
agent self-selection. The generator *guarantees* coverage constraints rather
than hoping random sampling hits them — at small agent counts, random draws
alone can't be trusted to put a ``solvable:false`` task in front of every agent.

Interpretation of the rule parameters:

* ``tasks_per_agent`` — exact number of task slots each agent receives.
* ``broken_fraction`` — fraction of each agent's slots drawn from
  ``solvable:false`` tasks (rounded; bumped to ≥1 when
  ``every_agent_gets_broken`` is set and any broken task exists).
* ``overlap_rate`` — fraction of agents that additionally share one common
  solvable task-id (each as its *own* instance, so no answer is directly
  copyable; doc 02 §3). This realizes "task slots shared by 2+ agents."

Every assignment gets a unique ``instance_tag`` so fresh containers and
re-randomized constants are the default even for overlapping task-ids.
"""

from __future__ import annotations

import datetime as _dt
import random

from agentlab.config import AgentConfig, AssignmentRules
from agentlab.tasks.models import Manifest, TaskInstance
from agentlab.tasks.repo import TaskRepo


def generate_manifest(
    repo: TaskRepo,
    *,
    run_id: str,
    seed: int,
    agents: list[AgentConfig],
    rules: AssignmentRules,
    instance_prefix: str = "",
) -> Manifest:
    """Build the one manifest for a run (or one wave of a longitudinal run).

    ``instance_prefix`` lets a longitudinal run tag each wave's instances
    distinctly (doc 02 §6) while drawing from the same repo.
    """
    rng = random.Random(seed)
    calibration = [tid for tid in repo.task_ids() if repo.load_meta(tid).calibration]
    if len(calibration) > 1:
        raise ValueError("task repo may define at most one calibration task")
    solvable = [tid for tid in repo.ids_by_solvable(True) if tid not in calibration]
    broken = [tid for tid in repo.ids_by_solvable(False) if tid not in calibration]

    instances: dict[str, TaskInstance] = {}
    assignments: dict[str, list[str]] = {a.agent_id: [] for a in agents}

    def new_instance(task_id: str) -> str:
        tag = f"{instance_prefix}{task_id}-{len(instances):04d}"
        inst = repo.instantiate(task_id, seed=seed, instance_tag=tag)
        instances[inst.instance_id] = inst
        return inst.instance_id

    # Decide the overlap cohort up front (deterministic).
    n_overlap_agents = round(rules.overlap_rate * len(agents)) if solvable else 0
    overlap_agent_ids: set[str] = set()
    overlap_task: str | None = None
    if n_overlap_agents >= 2 and solvable:
        overlap_task = rng.choice(solvable)
        overlap_agent_ids = {
            a.agent_id for a in rng.sample(agents, min(n_overlap_agents, len(agents)))
        }

    for agent in agents:
        # Calibration is a throwaway pre-task and does not consume one of the
        # scenario's configured main-task slots.
        if calibration:
            assignments[agent.agent_id].append(new_instance(calibration[0]))

        tpa = rules.tasks_per_agent
        n_broken = round(rules.broken_fraction * tpa)
        if rules.every_agent_gets_broken and broken and n_broken == 0:
            n_broken = 1
        n_broken = min(n_broken, tpa)
        n_solvable = tpa - n_broken

        # Solvable slots first so each agent's *first* instance — which the
        # orchestrator treats as the one real calibration task (doc 04 §3.3) —
        # is solvable and its trace check is meaningful.
        picks: list[str] = []
        for _ in range(n_solvable):
            if solvable:
                picks.append(rng.choice(solvable))
        for _ in range(n_broken):
            if broken:
                picks.append(rng.choice(broken))

        # Force the shared overlap task into one solvable slot for cohort agents.
        if overlap_task and agent.agent_id in overlap_agent_ids and overlap_task not in picks:
            solvable_slots = [i for i, t in enumerate(picks) if t in solvable]
            if solvable_slots:
                picks[solvable_slots[0]] = overlap_task
            else:
                picks.append(overlap_task)

        for task_id in picks:
            assignments[agent.agent_id].append(new_instance(task_id))

    manifest = Manifest(
        run_id=run_id,
        seed=seed,
        generated_at=_dt.datetime.now(_dt.UTC).isoformat(),
        assignments=assignments,
        instances=instances,
    )
    _assert_coverage(manifest, rules, broken_ids=set(broken))
    return manifest


def _assert_coverage(manifest: Manifest, rules: AssignmentRules, *, broken_ids: set[str]) -> None:
    """Fail loudly if a guaranteed constraint was not met (doc 02 §4)."""
    if rules.every_agent_gets_broken and broken_ids:
        for agent_id, iids in manifest.assignments.items():
            has_broken = any(manifest.instances[iid].task_id in broken_ids for iid in iids)
            if not has_broken:
                raise AssertionError(
                    f"coverage guarantee violated: agent {agent_id} has no broken task"
                )
