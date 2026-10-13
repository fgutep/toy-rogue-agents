#!/usr/bin/env python3
"""Drive the Scenario 3 multi-wave run with a persistent shared cache.

There is no built-in CLI for the wave schedule on purpose (docs 05/07): each
wave is a deliberate step and the Armory/honeypot/event volumes must survive
*between* waves — a ``down -v`` mid-run would wipe the shared state the whole
scenario exists to produce. This script keeps a single stable compose project
across all waves so those named volumes persist, recycling only the per-wave
agents/targets, and it never tears anything down. The operator collects
evidence and runs the final ``down -v`` by hand afterwards.

Because it launches agent containers, run it yourself (the assistant cannot).

    uv run python scripts/run_waves.py \
        --scenario experiments/incident_replication/scenarios/scenario3_waves \
        --run-root runs/scenario3-val --waves 3 --interval 90 --seed-note

Env: the usual run env must be loaded first (ARMORY_TOKEN, OPENAI_API_KEY, …);
`source scripts/load_env.fish`. AGENTLAB_RUN_DIR is set per wave by this script.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SEED_NOTE_PATH = "notes/ref_7f2a"


def _compose(project: str, wave_compose: Path, *args: str) -> list[str]:
    return [
        "docker", "compose", "-p", project,
        "-f", str(REPO / "docker/compose.yaml"),
        "-f", str(wave_compose), *args,
    ]


def _agents_running(project: str) -> int:
    out = subprocess.run(
        ["docker", "ps", "-q", "--filter", f"name={project}-agent-agent-"],
        capture_output=True, text=True, check=True,
    ).stdout
    return len([ln for ln in out.splitlines() if ln.strip()])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True, help="scenario3_waves directory")
    ap.add_argument("--run-root", required=True, help="parent dir for per-wave run dirs")
    ap.add_argument("--project", default="scenario3", help="stable compose project name")
    ap.add_argument("--waves", type=int, default=None,
                    help="override wave_count (e.g. 3 for a validation)")
    ap.add_argument("--interval", type=int, default=None, help="override inter-wave sleep, SECONDS")
    ap.add_argument("--seed-note", action="store_true",
                    help="plant the note before wave 0 (persists across waves)")
    ap.add_argument("--max-wait", type=int, default=1800,
                    help="max seconds to wait for a wave's agents to finish")
    args = ap.parse_args()

    # Imported here so --help works without the package env.
    from agentlab.orchestrator.docker_runner import DockerRunner
    from agentlab.scenarios import iter_wave_configs, load_wave_scenario

    schedule, template = load_wave_scenario(args.scenario)
    # Tasks live at the experiment root (…/incident_replication/tasks), i.e. the
    # scenario dir's grandparent — same resolution prepare-scenario uses.
    experiment_dir = Path(args.scenario).resolve().parent.parent
    configs = list(iter_wave_configs(schedule, template, experiment_dir=experiment_dir))
    if args.waves is not None:
        configs = configs[: args.waves]
    interval = args.interval if args.interval is not None else schedule.wave_interval_minutes * 60
    run_root = Path(args.run_root)

    print(f"Scenario 3: {len(configs)} wave(s), interval {interval}s, project '{args.project}', "
          f"seed_note={args.seed_note}")
    if not schedule.persist_armory_across_waves:
        print("ERROR: schedule does not persist the Armory across waves", file=sys.stderr)
        return 2

    for wave_num, cfg in enumerate(configs):
        wave_dir = run_root / f"wave{wave_num}"
        # Seed the note by letting wave 0 plant it (reuses the tested seeder +
        # the fail-closed wait); it then persists in the Armory for later waves.
        if wave_num == 0 and args.seed_note:
            cfg = cfg.model_copy(update={"plant_note": True, "note_path": SEED_NOTE_PATH})

        print(f"\n=== wave {wave_num}: preparing {wave_dir} ===")
        DockerRunner(cfg, wave_dir).prepare()

        env = dict(os.environ, AGENTLAB_RUN_DIR=str(wave_dir.resolve()))
        # --remove-orphans clears the previous wave's targets (and wave 0's
        # one-shot seeder) that are absent from this wave's config; the
        # persistent core services and named volumes are in compose.yaml and
        # stay. Without it they linger as orphans across waves.
        up = _compose(args.project, wave_dir / "agents.compose.yaml",
                      "up", "-d", "--build", "--remove-orphans")
        print(f"=== wave {wave_num}: up (detached) ===")
        subprocess.run(up, cwd=REPO, env=env, check=True)

        # Wait for this wave's agents to finish; core services stay up.
        print(f"=== wave {wave_num}: waiting for agents to finish ===")
        waited = 0
        while waited < args.max_wait:
            running = _agents_running(args.project)
            if running == 0:
                break
            time.sleep(5)
            waited += 5
        print(f"=== wave {wave_num}: agents done (waited {waited}s) ===")

        if wave_num + 1 < len(configs):
            print(f"=== sleeping {interval}s before wave {wave_num + 1} "
                  f"(volumes persist; NOT tearing down) ===")
            time.sleep(interval)

    print("\nAll waves launched. Nothing was torn down — the shared cache and")
    print("event store are intact. Collect evidence, then run the final:")
    print(f"  docker compose -p {args.project} -f docker/compose.yaml \\")
    print(f"    -f {run_root}/wave{len(configs)-1}/agents.compose.yaml down -v")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
