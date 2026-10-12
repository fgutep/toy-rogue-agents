# Task System — Repo, Assignment, and (for this run) Concrete Tasks

This document has two parts. **Part 1** is generalized — a task repo and assignment mechanism any experiment built on the shared agent framework (`01_agent_framework.md`) could reuse. **Part 2** is specific to the current experiment: the concrete task roster, the exact prompts/briefs agents receive, and the exact content of any planted artifact.

---

# Part 1 — Generalized Task Repo and Assignment

## 1. Goals

- A task should be a self-contained, versioned unit that can be instantiated fresh (new container, new random constants) on demand.
- Assignment of tasks to agents should be config-driven and reproducible, never left to agent self-selection.
- The same task repo should serve single-run experiments and multi-wave/longitudinal experiments without modification.

## 2. Task repo layout

```
tasks/
├── <task-id>/
│   ├── task.yaml           # metadata
│   ├── brief.md             # what the agent is told (see 2.1)
│   ├── target/               # Dockerfile + source for the target service
│   ├── score.sh              # pass/fail check, see 04_oracle_and_human_ui.md
│   └── params.schema.yaml      # which constants get re-randomized per instance
```

### 2.1 `task.yaml` schema

```yaml
id: <string>
category: <string>           # e.g. "web", "binary", "deserialization"
solvable: true | false          # ground truth, never exposed to agents
difficulty: easy | medium | hard
est_solve_steps: <int>          # calibration estimate, for budget sizing
brief_ref: brief.md
target_build: target/
score_cmd: score.sh
```

`solvable` is the critical hidden field — it is read only by the orchestrator and the oracle, never surfaced to an agent or even referenced in anything an agent can read. A "broken" task is otherwise indistinguishable in form from a solvable one — same brief structure, same target scaffolding, same scoring call.

## 3. Instantiation (why tasks are templates, not fixed instances)

Every task is a **template**. Instantiating it produces a fresh container plus a `params` dict (flag content, port offsets, any in-target constants) sampled from `params.schema.yaml` using the run's seed. This matters for two reasons:

1. It lets the same task template be reused across multiple agents (including intentionally overlapping assignments) without one agent's solution being directly copyable by another.
2. It lets the same template be reused across waves of a longitudinal run without later waves trivially inheriting earlier waves' answers from any shared artifact.

```python
def instantiate(task_id: str, seed: int, instance_tag: str) -> TaskInstance:
    params = sample_params(task_id, seed, instance_tag)
    container = build_and_launch_target(task_id, params)
    return TaskInstance(task_id, instance_tag, container, params)
```

## 4. Assignment manifest

A manifest is generated once per run (or once per wave, for longitudinal runs) by a small deterministic script, never hand-edited:

```yaml
run_id: <string>
seed: <int>
generated_at: <timestamp>
assignments:
  agent-01: [task-instance-id, task-instance-id, ...]
  agent-02: [...]
```

Assignment rules (parameters, not hardcoded logic):

```yaml
tasks_per_agent: <int>
broken_fraction: <float>        # fraction of each agent's slots drawn from solvable:false
overlap_rate: <float>           # fraction of task slots shared by 2+ agents (same task-id, different instance)
```

The generator guarantees, rather than merely hopes for, coverage — e.g. "every agent gets at least one `solvable:false` task" is an explicit constraint in the sampler, not left to chance, because at small agent counts random sampling alone can't be trusted to hit every cell.

The manifest itself is emitted as the first event on the telemetry sink before any agent starts, so ground truth about what was assigned is always available independent of anyone's notes.

## 5. Task delivery to an agent

An agent never reads `task.yaml` or the repo directly. At startup, the orchestrator resolves the agent's manifest entries into rendered briefs and appends them to the agent's initial message list (see `01_agent_framework.md` §3.2, `load_initial_tasks`). This keeps the trust boundary clean: whatever the agent can see is exactly and only the rendered `brief.md` plus whatever it discovers at runtime through its tools — nothing about the repo's internal metadata leaks by accident.

## 6. Reuse across longitudinal (multi-wave) runs

For an experiment that runs repeated waves over time with a persistent shared environment between waves (see `03_environment_instrumentation.md` for what persists), each wave gets its own manifest (new `instance_tag` per task, so fresh containers and re-randomized constants) while still drawing from the same `tasks/` repo. The repo itself does not need to know it's being used this way — that's an orchestration-layer decision.

---
