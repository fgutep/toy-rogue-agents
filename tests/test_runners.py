"""End-to-end validation of the local runner and the docker runner's file gen.

These use the shipped sample experiment with the mock backend: no spend, no
containers, no real experiment deployed — just verification that the harness is
wired correctly end to end.
"""

from __future__ import annotations

import json
from pathlib import Path

from agentlab.config import load_run_config
from agentlab.orchestrator.docker_runner import DockerRunner
from agentlab.orchestrator.local_runner import LocalRunner

LOCAL_CFG = "experiments/sample_experiment/run.local.yaml"
DOCKER_CFG = "experiments/sample_experiment/run.docker.yaml"


def test_local_sample_runs_end_to_end(tmp_path: Path):
    cfg = load_run_config(LOCAL_CFG)
    summary = LocalRunner(cfg, tmp_path / "run").run()

    assert set(summary.agent_results) == {"agent-01", "agent-02", "agent-03"}
    # Manifest + timeline artifacts produced.
    manifest = json.loads(Path(summary.manifest_path).read_text("utf-8"))
    assert manifest["run_id"] == "sample-local"
    timeline = json.loads(Path(summary.timeline_path).read_text("utf-8"))
    assert "merged_log" in timeline and timeline["merged_log"]

    # The oracle logged a correct grade for the solvable task and no correct
    # grade for a broken one (ground truth), proving discovery + grading worked.
    from agentlab.eventstore import EventStore

    store = EventStore(summary.event_db)
    grades = [e.payload for e in store.all_events() if e.kind == "grade"]
    assert any(g["real_correct"] for g in grades)  # solvable solved
    # Broken task is never correctly solved.
    assert all(not g["real_correct"] or g["task_id"] != "broken-token" for g in grades)
    # Each agent's first (solvable) submission is its one real calibration check.
    calibration = [g for g in grades if g["is_calibration"]]
    assert calibration and all(g["real_trace_matches_timeline"] is True for g in calibration)


def test_docker_runner_prepare_generates_hardened_services(tmp_path: Path):
    cfg = load_run_config(DOCKER_CFG)
    runner = DockerRunner(cfg, tmp_path / "run")
    manifest = runner.prepare()

    assert (tmp_path / "run" / "manifest.json").exists()
    compose = (tmp_path / "run" / "agents.compose.yaml").read_text("utf-8")
    # Hardening defaults are present in generated agent services.
    assert "cap_drop" in compose and "read_only" in compose and "no-new-privileges" in compose
    # Per-agent inputs written.
    for agent_id in manifest.assignments:
        adir = tmp_path / "run" / "agents" / agent_id
        assert (adir / "config.yaml").exists()
        assert (adir / "initial.json").exists()
        # Briefs carry the instance id but never a flag *value*.
        initial = (adir / "initial.json").read_text("utf-8")
        assert "INSTANCE_ID=" in initial
        for iid in manifest.assignments[agent_id]:
            flag = manifest.instances[iid].params.get("flag")
            if flag:
                assert flag not in initial


def test_dry_run_commands_do_not_execute(tmp_path: Path):
    cfg = load_run_config(DOCKER_CFG)
    runner = DockerRunner(cfg, tmp_path / "run")
    runner.prepare()
    cmd = runner.up(dry_run=True)
    assert "up" in cmd and "compose.yaml" in " ".join(cmd)
