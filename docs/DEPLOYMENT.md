# Deployment — Ubuntu 24.04 host

This guide sets up a containerized run on a dedicated, isolated Ubuntu 24.04
machine. Do **not** run experiments on a developer workstation; the whole point
of the containment layer is that it runs on a host you are willing to treat as
compromised.

## 1. Prerequisites

```bash
# Docker Engine + compose plugin
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-v2
sudo systemctl enable --now docker

# uv (for the orchestrator CLI, which runs on the host, outside containers)
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Clone the repo and install the orchestrator:

```bash
git clone <this-repo> agentlab && cd agentlab
uv sync
```

## 2. Secrets and environment

Secrets are read from the host environment and injected per container; they are
never baked into images or committed. Set what your run uses:

```bash
export ARMORY_TOKEN="$(openssl rand -hex 24)"   # the single shared Armory token
export OPENAI_API_KEY=...                        # and LUNA_/TERRA_/MISTRAL_ keys
export AGENTLAB_RUN_DIR="$PWD/runs/$(date +%s)"  # absolute path for this run
```

## 3. Launch

`agentlab up` generates the manifest and per-agent container inputs, then brings
up the core mesh plus one hardened container per agent:

```bash
uv run agentlab up \
  --config experiments/sample_experiment/run.docker.yaml \
  --run-dir "$AGENTLAB_RUN_DIR"
```

Preview the compose command without executing it with `--dry-run`.

The operator dashboard is published only on the host loopback:
<http://127.0.0.1:8500>. It lives on a separate Docker network the agent
containers have no route to (doc 04 §2). If you need remote access, SSH-tunnel
it (`ssh -L 8500:127.0.0.1:8500 host`) — do not publish it on a public interface.

## 4. What is enforced, and where

Containment is infrastructure, not prompt text (doc 01 §3.1). Per agent
container, `agentlab up` sets:

- non-root user, read-only root filesystem (`/workspace` + `/tmp` writable only);
- `cap_drop: ALL`, `no-new-privileges`, default seccomp;
- `pids_limit`, `mem_limit`, `cpus` caps;
- no Docker socket, no host mount beyond the agent's own `/agent` (read-only)
  inputs and its workspace volume;
- outbound traffic forced through `egress-proxy`, which **fails closed** on any
  host not in the run's `egress_allowlist` and injects `X-Agent-Identity` from
  the container's network identity (agents cannot forge or observe it).

The only path from the agent side to the observation side is the **write-only**
telemetry sink. The event store, timeline tooling, and UI all live on the
host-only side and are never reachable from an agent.

Overriding a hardening default requires listing the key in an agent's
`hardening_overrides` in the run config; that justification travels into the run
record as a logged config event, so no silent weakening is possible.

## 5. Tear down and collect results

```bash
uv run agentlab down \
  --config experiments/sample_experiment/run.docker.yaml \
  --run-dir "$AGENTLAB_RUN_DIR"
```

The run's ground truth persists in `$AGENTLAB_RUN_DIR` (manifest) and the
`eventdata` volume (event store + blobs). Reconstruct the timeline offline:

```bash
# copy the event db out of the volume, then:
uv run agentlab timeline --db events.sqlite3 --out timeline.json
```

## 6. Longitudinal / multi-wave runs

Re-run `up` with a new `--run-dir` per wave and an `instance_prefix` (see
`agentlab.tasks.manifest.generate_manifest`) so each wave gets fresh instances
and re-randomized constants while drawing from the same task repo (doc 02 §6).
The Armory volume persists across waves by design, which is what makes shared
state between waves observable.
