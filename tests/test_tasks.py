"""Task system: param determinism, repo loading, manifest coverage + overlap."""

from __future__ import annotations

from pathlib import Path

import yaml

from agentlab.config import AgentConfig, AssignmentRules
from agentlab.tasks.manifest import generate_manifest
from agentlab.tasks.params import sample_params
from agentlab.tasks.repo import TaskRepo

SAMPLE = Path("experiments/sample_experiment/tasks")


def _write_schema(tmp: Path) -> Path:
    p = tmp / "params.schema.yaml"
    p.write_text(yaml.safe_dump({"flag": {"type": "flag", "length": 8}}), encoding="utf-8")
    return p


def test_params_deterministic_and_instance_unique(tmp_path: Path):
    schema = _write_schema(tmp_path)
    a = sample_params(schema, seed=1, task_id="t", instance_tag="x")
    a2 = sample_params(schema, seed=1, task_id="t", instance_tag="x")
    b = sample_params(schema, seed=1, task_id="t", instance_tag="y")
    assert a == a2  # reproducible
    assert a["flag"] != b["flag"]  # different instance → different flag


def test_repo_hides_solvable_and_renders_brief():
    repo = TaskRepo(SAMPLE)
    assert set(repo.task_ids()) == {"find-token", "broken-token"}
    inst = repo.instantiate("find-token", seed=5, instance_tag="i1")
    # The *value* of the flag never leaks into the brief (the brief may still
    # describe the FLAG{...} format, which is fine).
    assert inst.params["flag"] not in inst.rendered_brief
    assert inst.params["flag"].startswith("FLAG{")


def test_manifest_coverage_and_overlap():
    repo = TaskRepo(SAMPLE)
    agents = [
        AgentConfig(agent_id=f"agent-0{i}", model_backend="mock", system_prompt_ref="x")
        for i in range(1, 4)
    ]
    rules = AssignmentRules(tasks_per_agent=2, broken_fraction=0.5, overlap_rate=1.0,
                            every_agent_gets_broken=True)
    m = generate_manifest(repo, run_id="r", seed=7, agents=agents, rules=rules)

    # Every agent has a broken task (guaranteed coverage).
    for iids in m.assignments.values():
        task_ids = {m.instances[i].task_id for i in iids}
        assert "broken-token" in task_ids

    # Overlap: the shared solvable task appears for >1 agent as distinct instances.
    find_instances = [i for i in m.instances if m.instances[i].task_id == "find-token"]
    assert len(find_instances) >= 2
    assert len({m.instances[i].instance_tag for i in find_instances}) == len(find_instances)


def test_manifest_reproducible():
    repo = TaskRepo(SAMPLE)
    agents = [AgentConfig(agent_id="agent-01", model_backend="mock", system_prompt_ref="x")]
    rules = AssignmentRules(tasks_per_agent=2, broken_fraction=0.5)
    m1 = generate_manifest(repo, run_id="r", seed=9, agents=agents, rules=rules)
    m2 = generate_manifest(repo, run_id="r", seed=9, agents=agents, rules=rules)
    assert m1.assignments == m2.assignments
