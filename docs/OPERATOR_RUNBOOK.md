# Operator runbook — incident-replication scenarios

For the human operator launching Scenario 1/2/3 on the dedicated Ubuntu 24.04
host, once system specs have been upgraded and verified. This is a toy,
contained, edu-only replication of a known multi-agent failure mode
(`Implementation-Docs/00_context_and_motivation.md`); it is not a real
engagement and ships no exploit content beyond the toy, inert tasks under
`experiments/incident_replication/tasks`.

This file documents **currently implemented and verified behavior only**. Where
something is logged but not yet enforced in code, that is called out
explicitly so you don't rely on it. Re-read this file before each run — it
reflects the code as of commit `8238a06`; if the orchestrator or UI changes,
re-verify the "What is NOT yet enforced" section before trusting it again.

## 1. Credentials needed before the run

Set these in the host shell **before** `agentlab prepare`/`up`/`run-local`.
Nothing is baked into images or configs; secrets are read from the host
environment and injected per container (`docker/compose.yaml`,
`agentlab/config.py::_expand_env`).

| Variable | Required for | Notes |
|---|---|---|
| `ARMORY_TOKEN` | every containerized run | single shared bearer token for the Armory cache service. `docker/compose.yaml` fails to start the `armory` service without it. Generate with `openssl rand -hex 24`. |
| `AGENTLAB_RUN_DIR` | every containerized run | absolute path to this run's directory (holds `manifest.json`); mounted read-only into `oracle` and `ui`. |
| `OPENAI_API_KEY` | Scenarios 1, 2, 3 (`model_backend: openai` in every current scenario YAML) | read by `agentlab/adapters/openai_adapter.py` / `_openai_compat.py`. |
| `LUNA_API_KEY` | only if an agent's `model_backend: luna` | not used by the current scenario files, but used in `experiments/sample_experiment`. |
| `TERRA_API_KEY` | only if an agent's `model_backend: terra` | same. |
| `MISTRAL_API_KEY` | only if an agent's `model_backend: codestral` | same. |
| A **second, independent** model credential | running `agentlab forensics` after any scenario | `ensure_independent_family()` (`agentlab/forensics/runner.py`) hard-fails if the forensics backend matches any backend the run actually used. All current scenarios use `openai`, so forensics needs a *different* family's key (e.g. `MISTRAL_API_KEY` for `codestral`, or `LUNA_API_KEY`) — decide this before the run so the key is on hand afterward. |

No credential is a precondition for `run-local` against the mock backend, or
for `agentlab prepare`/`manifest`/`scenario-config`/`prepare-scenario` — those
commands do not read provider keys and do not launch containers or spend
tokens.

## 2. Preflight (repeat before every run, not just once)

```bash
cd /home/felipe/Documents/toy-rogue-agents
uv run --extra dev pytest -q               # full suite against the mock backend; no spend
docker info                                # confirm daemon reachable, check driver/cgroup version
df -h .                                    # free disk for runs/ + volumes
timedatectl                                # NTP sync matters for event ordering/timeline
ss -ltn | grep -E ':8500|:8800|:8700|:8600|:3128'   # ports agentlab wants must be free
```

Confirm no other `agentlab`/compose project is already up (`docker compose ls`,
`docker ps`) — stale containers from a previous aborted run can hold the named
volumes (`eventdata`, `armorydata`) and ports.

## 3. Dry runs first — every time

Never launch directly into `up`. Walk the non-destructive steps first and read
their output:

```bash
# Render the scenario into a plain run config without touching anything:
uv run agentlab scenario-config \
  --scenario experiments/incident_replication/scenarios/scenario1_no_note.yaml \
  --out runs/scenario1/run.yaml

# Generate the manifest + compose inputs only — still nothing launched:
uv run agentlab prepare-scenario \
  --scenario experiments/incident_replication/scenarios/scenario1_no_note.yaml \
  --run-dir runs/scenario1

# Inspect what would actually run before committing to it:
cat runs/scenario1/manifest.json
cat runs/scenario1/agents.compose.yaml

# Preview the exact docker compose invocation agentlab would issue:
uv run agentlab up --config runs/scenario1/run.yaml --run-dir runs/scenario1 --dry-run
```

Only once the manifest, the rendered agents compose file, and the dry-run
command all look right should you proceed to a live `up`.

## 4. Launching Scenario 1 / Scenario 2

```bash
export ARMORY_TOKEN="$(openssl rand -hex 24)"
export OPENAI_API_KEY=...
export AGENTLAB_RUN_DIR="$PWD/runs/scenario1"   # must match --run-dir below

uv run agentlab up \
  --config runs/scenario1/run.yaml \
  --run-dir "$AGENTLAB_RUN_DIR"
```

**`agentlab up` runs in the foreground and blocks the terminal** — it execs
`docker compose ... up --build` with no `-d`. Keep this terminal for the run
(`Ctrl+C` stops the attached compose process; see §6 for what that does and
does not do), and open a **second terminal** for the operator UI and
monitoring.

Scenario 2 is identical except its scenario YAML sets `plant_note: true`.
Per `experiments/incident_replication/README.md`, the planted note must be
persisted as an orchestrator event **before** the agents start — confirm this
happened by checking the event store for the planted-note event before
treating the run as started, not by assuming the scenario config alone did it:

```bash
uv run agentlab timeline --db runs/scenario2/events.sqlite3 | grep -i note
```

If it is not there, stop the run (§6) rather than let agents proceed without
the planted artifact the scenario depends on.

Open the operator dashboard (second terminal), loopback-only:

```
http://127.0.0.1:8500
```

It is on `ui-host`, a network the agent containers have no route to. For
remote access, SSH-tunnel — never publish it on a non-loopback interface:

```bash
ssh -L 8500:127.0.0.1:8500 <host>
```

## 5. Monitoring during the run

- Watch `/api/events` / the websocket feed in the dashboard, or poll from the
  shell: `curl -s http://127.0.0.1:8500/api/events?since_seq=0 | jq`.
- The dashboard's pause/resume/kill buttons POST to
  `/api/operator/action` and **are logged as first-class `operator_*` events**
  — that logging is real and will show up in the reconstructed timeline.
- **They do not currently stop anything by themselves.** As of this commit,
  nothing in `agentlab/orchestrator/` watches the event store for
  `operator_pause`/`operator_kill` events and acts on them — the docstring in
  `agentlab/ui/backend.py` describes the intended design ("the orchestrator
  enforces it"), but no watcher exists yet. If you need a container actually
  paused or stopped mid-run, do it directly against Docker (§6), then still
  press the matching UI button so the operator intervention is captured in the
  same timeline as everything else. Don't rely on the UI button alone to
  protect anything.
- The event store, timeline, and UI are host-only and read-only over the
  write-only telemetry sink; an agent cannot observe any of this (doc 04 §2–3).
  If you see evidence an agent is reacting to something only an operator
  should know, that is itself a finding — treat it as a containment bug, not
  expected behavior, and stop the run to investigate.

## 6. Stopping a run — evidence-safe

`agentlab down` currently runs `docker compose down -v`, i.e. **it deletes the
named Docker volumes** (`eventdata`, `armorydata`) along with the containers.
Those volumes hold the real event store and the Armory cache state — the
primary evidence of the run. Never run `agentlab down` as your first move.

To stop a run while preserving evidence:

1. **Copy evidence out of the volumes first**, while they still exist:

   ```bash
   docker run --rm -v eventdata:/data -v "$PWD/runs/scenario1/evidence:/out" \
     busybox cp -a /data/. /out/
   docker run --rm -v armorydata:/data -v "$PWD/runs/scenario1/evidence-armory:/out" \
     busybox cp -a /data/. /out/
   ```

2. **Stop containers without the engineered `-v` teardown**, so the volumes
   survive in case step 1 needs to be redone or extended:

   ```bash
   docker compose -f docker/compose.yaml -f runs/scenario1/agents.compose.yaml stop
   # or, to also remove containers but keep volumes:
   docker compose -f docker/compose.yaml -f runs/scenario1/agents.compose.yaml down
   ```

3. Only after evidence is safely copied out, run `agentlab down` (or `docker
   compose down -v`) if you want the volumes removed too.

If you only pressed `Ctrl+C` on the foreground `up` process (§4): compose
sends a stop signal to the containers but does **not** remove volumes or the
compose project by itself; the containers may still be present (`docker ps
-a`) and the volumes intact — confirm with `docker ps -a` / `docker volume ls`
before assuming anything was torn down.

## 7. Post-run: timeline and forensics

```bash
# Reconstruct the merged timeline / provenance / influence graph from the
# copied-out event store (not the live volume, so this never races teardown):
uv run agentlab timeline --db runs/scenario1/evidence/events.sqlite3 \
  --out runs/scenario1/timeline.json

# Independent-family forensics classifier (read-only, post-hoc):
export MISTRAL_API_KEY=...   # or another family NOT used by the run itself
uv run agentlab forensics \
  --db runs/scenario1/evidence/events.sqlite3 \
  --run-id scenario1_no_note \
  --backend codestral --model codestral-latest
```

`run_forensics` raises immediately if `--backend` matches any backend the run
itself used (openai in every current scenario) — that's `doc 06`'s
independence requirement, enforced in code, not just convention.

Every grade already logs real vs. agent-facing facts side by side (oracle,
doc 04); forensics is a separate, later, read-only pass over the same
persisted events and never re-contacts the agents or the toy targets.

## 8. Scenario 3 (persistent multi-wave) — do not run unattended

`experiments/incident_replication/scenarios/scenario3_waves/` describes 8
waves at a 45-minute interval, with the Armory **and** the honeypot
persisting across waves by design (`persist_armory_across_waves: true`,
`persist_honeypot_across_waves: true`) — that persistence is the point (doc 02
§6), but it also means a mistake in wave *N* contaminates every later wave.

As of this commit there is no single `agentlab` command that drives the
8-wave schedule end-to-end; each wave is a separate `up`/`down` cycle against
the same `experiments/incident_replication` task repo with a fresh `--run-dir`
and an `instance_prefix` so instances/constants re-randomize per wave
(`agentlab.tasks.manifest.generate_manifest`, doc 02 §6) while the shared
Armory/honeypot volumes persist intentionally. Concretely, per wave:

```bash
uv run agentlab scenario-config \
  --scenario experiments/incident_replication/scenarios/scenario3_waves/wave_manifest_template.yaml \
  --out runs/scenario3-wave1/run.yaml
# edit the rendered run.yaml's manifest/instance_prefix per wave before `up`,
# per wave_schedule.yaml's wave_count / wave_interval_minutes
```

Do not script unattended looping over this yet. Launch each wave as a
deliberate, human-reviewed step until a tested wave-driver exists, and apply
§3's dry-run discipline and §6's evidence-safe stop to every wave individually
— `down -v` between waves would destroy the exact cross-wave state the
scenario exists to produce.

## 9. If something looks wrong

Stop (§6, evidence-safe) rather than debug live. This lab is instrumented
specifically so a stopped run's full history survives teardown (once copied
out) — reconstruct and review offline rather than improvising against a live
agent population.
