from __future__ import annotations

from pathlib import Path

import yaml

from agentlab.scenarios.models import ScenarioConfig, WaveManifestTemplate, WaveSchedule


def load_scenario(path: str | Path) -> ScenarioConfig:
    source = Path(path)
    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    scenario = ScenarioConfig.model_validate(data)
    if source.stem != scenario.scenario_id:
        raise ValueError(
            f"scenario_id {scenario.scenario_id!r} does not match filename {source.stem!r}"
        )
    return scenario


def load_wave_scenario(path: str | Path) -> tuple[WaveSchedule, WaveManifestTemplate]:
    root = Path(path)
    schedule = WaveSchedule.model_validate(
        yaml.safe_load((root / "wave_schedule.yaml").read_text(encoding="utf-8"))
    )
    template = WaveManifestTemplate.model_validate(
        yaml.safe_load((root / "wave_manifest_template.yaml").read_text(encoding="utf-8"))
    )
    if root.name != schedule.scenario_id:
        raise ValueError(
            f"scenario_id {schedule.scenario_id!r} does not match directory {root.name!r}"
        )
    if not schedule.persist_armory_across_waves or not schedule.persist_honeypot_across_waves:
        raise ValueError("scenario3 requires Armory and honeypot persistence across waves")
    return schedule, template
