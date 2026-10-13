"""agentlab command-line interface.

    agentlab run-local  --config <run.yaml> --run-dir <dir>   # in-process, no Docker
    agentlab prepare    --config <run.yaml> --run-dir <dir>   # generate container inputs
    agentlab up|down    --config <run.yaml> --run-dir <dir>   # docker compose (host)
    agentlab manifest   --config <run.yaml> --out <file>      # manifest only
    agentlab timeline   --db <events.sqlite3> --out <file>    # reconstruct timeline
"""

from __future__ import annotations

import json

import click

from agentlab.config import load_run_config


@click.group()
def main() -> None:
    """Contained laboratory for tool-using LLM agents."""


@main.command("run-local")
@click.option("--config", "config_path", required=True)
@click.option("--run-dir", "run_dir", required=True)
def run_local(config_path: str, run_dir: str) -> None:
    """Run an experiment end-to-end in-process (mock backend; no containment)."""
    from agentlab.orchestrator.local_runner import LocalRunner

    summary = LocalRunner(load_run_config(config_path), run_dir).run()
    click.echo(json.dumps(summary.__dict__, indent=2))


@main.command("prepare")
@click.option("--config", "config_path", required=True)
@click.option("--run-dir", "run_dir", required=True)
@click.option("--agent-image", default="agentlab-agent:latest")
def prepare(config_path: str, run_dir: str, agent_image: str) -> None:
    """Generate the manifest and all per-agent container inputs + compose file."""
    from agentlab.orchestrator.docker_runner import DockerRunner

    manifest = DockerRunner(load_run_config(config_path), run_dir, agent_image).prepare()
    click.echo(f"prepared {len(manifest.assignments)} agents in {run_dir}")


@main.command("up")
@click.option("--config", "config_path", required=True)
@click.option("--run-dir", "run_dir", required=True)
@click.option("--dry-run", is_flag=True)
def up(config_path: str, run_dir: str, dry_run: bool) -> None:
    """Bring up the containerized run (Ubuntu host). Prepares inputs first."""
    from agentlab.orchestrator.docker_runner import DockerRunner

    runner = DockerRunner(load_run_config(config_path), run_dir)
    runner.prepare()
    cmd = runner.up(dry_run=dry_run)
    click.echo(("would run: " if dry_run else "ran: ") + " ".join(cmd))


@main.command("down")
@click.option("--config", "config_path", required=True)
@click.option("--run-dir", "run_dir", required=True)
@click.option("--dry-run", is_flag=True)
def down(config_path: str, run_dir: str, dry_run: bool) -> None:
    """Tear down the containerized run (Ubuntu host)."""
    from agentlab.orchestrator.docker_runner import DockerRunner

    runner = DockerRunner(load_run_config(config_path), run_dir)
    cmd = runner.down(dry_run=dry_run)
    click.echo(("would run: " if dry_run else "ran: ") + " ".join(cmd))


@main.command("manifest")
@click.option("--config", "config_path", required=True)
@click.option("--out", "out_path", default=None)
def manifest(config_path: str, out_path: str | None) -> None:
    """Generate the assignment manifest without running anything."""
    from agentlab.tasks.manifest import generate_manifest
    from agentlab.tasks.repo import TaskRepo

    cfg = load_run_config(config_path)
    repo = TaskRepo(cfg.experiment_path() / "tasks")
    m = generate_manifest(repo, run_id=cfg.run_id, seed=cfg.seed,
                          agents=cfg.agents, rules=cfg.assignment)
    text = m.model_dump_json(indent=2)
    if out_path:
        open(out_path, "w", encoding="utf-8").write(text)
        click.echo(f"wrote manifest to {out_path}")
    else:
        click.echo(text)


@main.command("scenario-config")
@click.option("--scenario", "scenario_path", required=True)
@click.option("--out", "out_path", required=True)
@click.option("--run-id", default=None)
def scenario_config(scenario_path: str, out_path: str, run_id: str | None) -> None:
    """Render one declarative scenario into a normal run config; does not run it."""
    from pathlib import Path

    import yaml

    from agentlab.scenarios import load_scenario, scenario_to_run_config

    source = Path(scenario_path)
    cfg = scenario_to_run_config(
        load_scenario(source), experiment_dir=source.parent.parent, run_id=run_id
    )
    Path(out_path).write_text(
        yaml.safe_dump(cfg.model_dump(by_alias=True, exclude_none=True), sort_keys=False),
        encoding="utf-8",
    )
    click.echo(f"wrote run config to {out_path}; nothing was launched")


@main.command("prepare-scenario")
@click.option("--scenario", "scenario_path", required=True)
@click.option("--run-dir", "run_dir", required=True)
@click.option("--run-id", default=None)
def prepare_scenario(scenario_path: str, run_dir: str, run_id: str | None) -> None:
    """Generate manifest and Compose inputs for a scenario; launch nothing."""
    from pathlib import Path

    from agentlab.orchestrator.docker_runner import DockerRunner
    from agentlab.scenarios import load_scenario, scenario_to_run_config

    source = Path(scenario_path)
    cfg = scenario_to_run_config(
        load_scenario(source), experiment_dir=source.parent.parent, run_id=run_id
    )
    manifest = DockerRunner(cfg, run_dir).prepare()
    click.echo(f"prepared {len(manifest.assignments)} agents in {run_dir}; nothing was launched")


@main.command("forensics")
@click.option("--db", "db_path", required=True)
@click.option("--run-id", required=True)
@click.option("--backend", required=True)
@click.option("--model", required=True)
@click.option("--scenario-id", default=None)
def forensics(
    db_path: str, run_id: str, backend: str, model: str, scenario_id: str | None
) -> None:
    """Run the read-only post-run classifier after an experiment has ended."""
    from agentlab.adapters import build_adapter
    from agentlab.eventstore import EventStore
    from agentlab.forensics import ensure_independent_family, run_forensics

    store = EventStore(db_path)
    ensure_independent_family(store, backend)
    adapter = build_adapter(backend, {"model": model})
    records, summary = run_forensics(
        store,
        run_id=run_id,
        adapter=adapter,
        forensics_model=model,
        scenario_id=scenario_id,
    )
    click.echo(json.dumps({
        "records": len(records), "summary": summary.model_dump()
    }, indent=2))


@main.command("timeline")
@click.option("--db", "db_path", required=True)
@click.option("--out", "out_path", default=None)
def timeline(db_path: str, out_path: str | None) -> None:
    """Reconstruct a run's timeline from its event store."""
    from agentlab.eventstore import EventStore
    from agentlab.reconstruct_timeline import reconstruct, timeline_to_dict

    tl = timeline_to_dict(reconstruct(EventStore(db_path)))
    text = json.dumps(tl, indent=2)
    if out_path:
        open(out_path, "w", encoding="utf-8").write(text)
        click.echo(f"wrote timeline to {out_path}")
    else:
        click.echo(text)


if __name__ == "__main__":
    main()
