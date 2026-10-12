# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`agentlab` is a generalized, contained laboratory for running small populations of tool-using LLM agents with fully external observation (the design lives in `Implementation-Docs/`, docs 01–04). It reproduces a known multi-agent failure mode — agents coordinating through a shared under-isolated cache and forming an unwarranted belief about grading — inside a sandbox, for study. The framework ships no task content or hypotheses; experiments plug in under `experiments/`.

## Commands

The project uses **uv**. Python 3.12+.

```bash
uv sync --extra dev                         # install (creates .venv, writes uv.lock)
uv run --extra dev pytest -q                # full test suite (mock backend, no spend)
uv run --extra dev pytest tests/test_tasks.py::test_manifest_coverage_and_overlap  # one test
uv run --extra dev ruff check agentlab tests        # lint
uv run agentlab run-local --config experiments/sample_experiment/run.local.yaml --run-dir runs/x
uv run agentlab timeline --db runs/x/events.sqlite3 # reconstruct a run's timeline
uv run agentlab prepare|up|down --config <run.yaml> --run-dir <dir>   # docker path (Ubuntu host)
```

`run-local` runs the whole pipeline in-process against the mock backend (no containers, no API keys). The containerized path (`up`/`down`) is for the Ubuntu 24.04 deployment host — see `docs/DEPLOYMENT.md`.

## Architecture (the big picture)

Data flows one way from agents to observers. An agent only ever sees its rendered brief plus what it discovers through tools; everything else is external.

- **Canonical layer** (`schema.py`): `Message`/`ToolCall`/`ToolSpec`/`ToolResult`. The loop, logging, and tool dispatch speak only these. Provider formats never leak past an adapter.
- **Adapters** (`adapters/`): `ModelAdapter` ABC; `ADAPTERS` registry maps a config key (`openai`,`luna`,`terra`,`codestral`,`mock`) to a lazily-imported class. OpenAI/Luna/Terra share `_openai_compat.py`; Codestral is its own. Errors normalize to the `errors.py` taxonomy. Adding a backend = one module + one registry line.
- **Loop** (`loop.py`): system prompt + rendered briefs, then `adapter.chat` → `telemetry.emit` → dispatch tools → append `role="tool"` → repeat, until a step/token budget or termination. The orchestrator (not the agent) supplies prompt, briefs, registry, context, telemetry.
- **Tools** (`tools.py`): `ToolRegistry` decorator + generic tools (`shell_exec`, `read_file`/`write_file` scoped to the workspace, `http_request` fail-closed, `submit`). `ToolContext` carries identity, workspace, telemetry, egress policy, proxy, and the grader callable.
- **Tasks** (`tasks/`): `TaskRepo` loads templates; `params.py` samples per-instance constants deterministically from `(seed, task_id, instance_tag)`; `manifest.py` generates assignments with *guaranteed* coverage (every agent gets a broken task; overlap shares a solvable task as distinct instances).
- **Instrumentation**: `telemetry_sink/` (write-only inbound), `eventstore/` (append-only SQLite + content-addressed blobs), `armory/` (the shared cache), `honeypot/`, `reconstruct_timeline.py` (offline merged log + provenance + influence graph).
- **Oracle** (`oracle/`): cheap string-compare ground truth + a narrative "perceived-complexity" response; the per-agent *first* instance is the one real calibration check that gates on trace; everything after is flag-only. Every grade logs real vs. agent-facing facts side by side.
- **UI** (`ui/`): host-only read-only backend + websocket + dependency-free SPA. Not on the agents' network.
- **Proxy** (`proxy/`): single egress chokepoint; fails closed off the allowlist and injects transport-derived `X-Agent-Identity`.
- **Orchestrator** (`orchestrator/`): `LocalRunner` (in-process, for the sample/CI; not a containment boundary) and `DockerRunner` (generates the manifest + per-agent inputs + a hardened compose file; the only privileged component besides the sink). `agent_entry.py` is the in-container entrypoint.

## Invariants — treat as requirements, verified by tests

- **No context compaction** in the loop; transcripts grow until a step/token budget ends them. Budgets are step/token-based, never wall-clock.
- **An agent can never observe its own logging.** `telemetry.emit` returns `None` and has no read path; the sink is write-only; no tool reads persisted logs. Don't add one.
- **The hidden `solvable` field and flag values never reach an agent.** Briefs are rendered with the flag excluded; assignment metadata stays orchestrator-side.
- **Identity comes from the transport layer** (proxy-injected header), never a self-reported one.
- **Containment is infrastructure** (hardened containers + fail-closed egress proxy), never prompt text. Hardening overrides must be declared in config, which is logged.
- **Provider-specific types stay inside their adapter.**

When extending: add experiment tools by extending a copy of `build_default_registry()`; keep new cross-service events in the `Event` envelope so the timeline tool and UI pick them up for free. Don't run real experiments in a dev environment — that's the Ubuntu host's job.
