from __future__ import annotations

from pathlib import Path

import yaml

from agentlab.orchestrator.docker_runner import DockerRunner
from agentlab.scenarios import (
    derive_wave_seed,
    iter_wave_configs,
    load_scenario,
    load_wave_scenario,
    scenario_to_run_config,
)
from agentlab.tasks.manifest import generate_manifest
from agentlab.tasks.repo import TaskRepo

ROOT = Path("experiments/incident_replication")


def test_case_roster_and_broken_briefs_are_byte_identical():
    repo = TaskRepo(ROOT / "tasks")
    assert set(repo.task_ids()) == {
        "calibration-0", "sqli-a", "sqli-a-broken", "idor-a", "idor-a-broken",
        "overflow-a", "overflow-a-broken", "ssti-a", "ssti-a-broken",
    }
    for base in ("sqli-a", "idor-a", "overflow-a", "ssti-a"):
        assert (ROOT / "tasks" / base / "brief.md").read_bytes() == (
            ROOT / "tasks" / f"{base}-broken" / "brief.md"
        ).read_bytes()


def test_case_manifest_has_explicit_calibration_and_hides_flags():
    scenario = load_scenario(ROOT / "scenarios/scenario1_no_note.yaml")
    cfg = scenario_to_run_config(scenario, experiment_dir=ROOT)
    manifest = generate_manifest(
        TaskRepo(ROOT / "tasks"), run_id=cfg.run_id, seed=cfg.seed,
        agents=cfg.agents, rules=cfg.assignment,
    )
    for _agent_id, iids in manifest.assignments.items():
        assert len(iids) == 4  # calibration + three main tasks
        assert manifest.instances[iids[0]].calibration
        assert manifest.instances[iids[0]].task_id == "calibration-0"
        assert any("-broken" in manifest.instances[iid].task_id for iid in iids)
        for iid in iids:
            inst = manifest.instances[iid]
            assert inst.params.get("flag", "not-present") not in inst.rendered_brief
            assert inst.params["target_host"] in inst.rendered_brief


def test_controlled_pair_differs_only_by_note_fields():
    first = load_scenario(ROOT / "scenarios/scenario1_no_note.yaml")
    second = load_scenario(ROOT / "scenarios/scenario2_note_planted.yaml")
    left = first.model_dump(exclude={"scenario_id", "plant_note", "note_path"})
    right = second.model_dump(exclude={"scenario_id", "plant_note", "note_path"})
    assert left == right

    repo = TaskRepo(ROOT / "tasks")
    a = scenario_to_run_config(first, experiment_dir=ROOT, run_id="pair")
    b = scenario_to_run_config(second, experiment_dir=ROOT, run_id="pair")
    ma = generate_manifest(repo, run_id="pair", seed=a.seed, agents=a.agents, rules=a.assignment)
    mb = generate_manifest(repo, run_id="pair", seed=b.seed, agents=b.agents, rules=b.assignment)
    assert ma.assignments == mb.assignments
    assert {k: v.model_dump() for k, v in ma.instances.items()} == {
        k: v.model_dump() for k, v in mb.instances.items()
    }


def test_wave_seeds_and_configs_are_reproducible_and_distinct():
    schedule, template = load_wave_scenario(ROOT / "scenarios/scenario3_waves")
    seeds = [derive_wave_seed(schedule.seed, i) for i in range(schedule.wave_count)]
    assert seeds == [derive_wave_seed(schedule.seed, i) for i in range(schedule.wave_count)]
    assert len(set(seeds)) == schedule.wave_count
    configs = list(iter_wave_configs(schedule, template, experiment_dir=ROOT))
    assert len(configs) == 8
    assert all(not config.plant_note for config in configs)


def test_prepare_generates_targets_private_networks_and_note_gate(tmp_path: Path):
    scenario = load_scenario(ROOT / "scenarios/scenario2_note_planted.yaml")
    cfg = scenario_to_run_config(scenario, experiment_dir=ROOT)
    manifest = DockerRunner(cfg, tmp_path / "prepared").prepare()
    compose = yaml.safe_load((tmp_path / "prepared/agents.compose.yaml").read_text("utf-8"))
    services = compose["services"]
    assert "armory-seeder" in services
    target_names = {inst.params["target_host"] for inst in manifest.instances.values()}
    assert target_names <= services.keys()
    for agent in cfg.agents:
        service = services[f"agent-{agent.agent_id}"]
        assert len(service["networks"]) == 1
        assert service["networks"][0].endswith(agent.agent_id)
        assert (
            service["depends_on"]["armory-seeder"]["condition"]
            == "service_completed_successfully"
        )
        proxy = services[f"egress-proxy-{agent.agent_id}"]
        assert "agentlab-services" in proxy["networks"]
    assert all(spec.get("internal") for name, spec in compose["networks"].items()
               if name != "agentlab-services")


def test_custom_note_content_reaches_the_seeder(tmp_path: Path):
    # Scenario 4 plants a custom note (the honeypot lure); the override must
    # flow all the way into the seeder's NOTE_CONTENT, not the default note.
    from agentlab.scenarios import PLANTED_NOTE

    scenario = load_scenario(ROOT / "scenarios/scenario4_honeypot_lure.yaml")
    assert scenario.note_content and "honeypot:8700" in scenario.note_content
    cfg = scenario_to_run_config(scenario, experiment_dir=ROOT)
    assert cfg.note_content == scenario.note_content
    DockerRunner(cfg, tmp_path / "p").prepare()
    compose = yaml.safe_load((tmp_path / "p/agents.compose.yaml").read_text("utf-8"))
    content = compose["services"]["armory-seeder"]["environment"]["NOTE_CONTENT"]
    assert "honeypot:8700" in content
    assert content != PLANTED_NOTE
