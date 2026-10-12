"""Docker/compose run driver — the privileged orchestrator for real runs (doc 01 §4, §3.1).

Unlike the local runner, this path enforces containment at the infrastructure
layer: each agent is its own hardened container, joined only to the agents'
bridge network, with egress forced through the proxy sidecar (doc 01 §3.1). This
module is the *only* privileged component besides the telemetry sink.

It does not itself run experiments in this repo's dev environment; it generates
the per-run artifacts and a compose file and (on the Ubuntu host) invokes
``docker compose``. :meth:`prepare` is pure file generation and is unit-tested;
:meth:`up`/:meth:`down` shell out and are meant for the deployment host.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import yaml

from agentlab.config import RunConfig
from agentlab.tasks.manifest import generate_manifest
from agentlab.tasks.models import Manifest

# Non-negotiable hardening defaults (doc 01 §3.1). Overridable only with an
# explicit, logged justification in the agent's config.hardening_overrides.
_HARDENING = {
    "read_only": True,
    "cap_drop": ["ALL"],
    "security_opt": ["no-new-privileges:true", "seccomp=default"],
    "pids_limit": 256,
    "mem_limit": "1g",
    "cpus": "1.0",
    "user": "10001:10001",
}


class DockerRunner:
    def __init__(self, config: RunConfig, run_dir: str | Path,
                 agent_image: str = "agentlab-agent:latest") -> None:
        self.config = config
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.agent_image = agent_image
        self.repo_tasks = self.config.experiment_path() / "tasks"

    def prepare(self) -> Manifest:
        """Generate the manifest and all per-agent container inputs + compose file."""
        from agentlab.tasks.repo import TaskRepo

        repo = TaskRepo(self.repo_tasks)
        manifest = generate_manifest(
            repo, run_id=self.config.run_id, seed=self.config.seed,
            agents=self.config.agents, rules=self.config.assignment,
        )
        (self.run_dir / "manifest.json").write_text(
            manifest.model_dump_json(indent=2), encoding="utf-8"
        )

        services: dict[str, dict] = {}
        for agent in self.config.agents:
            adir = self.run_dir / "agents" / agent.agent_id
            adir.mkdir(parents=True, exist_ok=True)
            (adir / "config.yaml").write_text(
                yaml.safe_dump(agent.model_dump(by_alias=True)), encoding="utf-8"
            )
            (adir / "system_prompt.md").write_text(
                (self.config.experiment_path() / agent.system_prompt_ref).read_text("utf-8"),
                encoding="utf-8",
            )
            initial = []
            for iid in manifest.assignments[agent.agent_id]:
                inst = manifest.instances[iid]
                initial.append({
                    "role": "user",
                    "content": f"INSTANCE_ID={iid}\n\n{inst.rendered_brief}\n",
                })
            (adir / "initial.json").write_text(json.dumps(initial, indent=2), encoding="utf-8")

            services[f"agent-{agent.agent_id}"] = self._agent_service(agent, adir)

        compose = {
            "services": services,
            # Declared (not external) so the merged compose (base -f + this -f)
            # resolves the network; base/compose.yaml declares the same name.
            "networks": {self.config.network.bridge_name: {}},
            "volumes": {f"ws-{a.agent_id}": {} for a in self.config.agents},
        }
        (self.run_dir / "agents.compose.yaml").write_text(
            yaml.safe_dump(compose, sort_keys=False), encoding="utf-8"
        )
        return manifest

    def _agent_service(self, agent, adir: Path) -> dict:
        hardening = dict(_HARDENING)
        # Overrides must be justified; the justification travels into the run
        # record via the agent config, which is logged as a run event (doc 01 §4).
        for key in agent.hardening_overrides:
            hardening.pop(key, None)
        svc = {
            "image": self.agent_image,
            "command": ["python", "-m", "agentlab.agent_entry"],
            "networks": [self.config.network.bridge_name],
            "environment": {
                "AGENTLAB_AGENT_CONFIG": "/agent/config.yaml",
                "AGENTLAB_SYSTEM_PROMPT": "/agent/system_prompt.md",
                "AGENTLAB_INITIAL_MSGS": "/agent/initial.json",
                "AGENTLAB_WORKSPACE": "/workspace",
                "AGENTLAB_TELEMETRY": self.config.telemetry_url,
                # Reached through the egress proxy (the single chokepoint); the
                # proxy injects X-Agent-Identity for internal services.
                "AGENTLAB_ORACLE_URL": "http://oracle:8600/submit",
                "AGENTLAB_PROXY_URL": "http://egress-proxy:3128",
                "AGENTLAB_EGRESS_ALLOW": ",".join(self.config.network.egress_allowlist),
                # Backend API keys flow in from the host env, never baked in.
                **{k: f"${{{k}}}" for k in _api_key_env_for(agent.model_backend)},
            },
            "volumes": [
                {"type": "bind", "source": str(adir), "target": "/agent", "read_only": True},
                {"type": "volume", "source": f"ws-{agent.agent_id}", "target": "/workspace"},
                {"type": "tmpfs", "target": "/tmp"},
            ],
            "read_only": hardening.get("read_only", True),
            "cap_drop": hardening.get("cap_drop", ["ALL"]),
            "security_opt": hardening.get("security_opt", []),
            "pids_limit": hardening.get("pids_limit", 256),
            "mem_limit": hardening.get("mem_limit", "1g"),
            "cpus": hardening.get("cpus", "1.0"),
            "user": hardening.get("user", "10001:10001"),
            "restart": "no",
        }
        return {k: v for k, v in svc.items() if v is not None}

    # -- host-side actions ------------------------------------------------ #
    def up(self, *, dry_run: bool = False) -> list[str]:
        cmd = [
            self._compose(), "-f", "docker/compose.yaml",
            "-f", str(self.run_dir / "agents.compose.yaml"), "up", "--build",
        ]
        if not dry_run:
            subprocess.run(cmd, check=True)
        return cmd

    def down(self, *, dry_run: bool = False) -> list[str]:
        cmd = [
            self._compose(), "-f", "docker/compose.yaml",
            "-f", str(self.run_dir / "agents.compose.yaml"), "down", "-v",
        ]
        if not dry_run:
            subprocess.run(cmd, check=True)
        return cmd

    @staticmethod
    def _compose() -> str:
        return shutil.which("docker") or "docker"


def _api_key_env_for(backend: str) -> list[str]:
    return {
        "openai": ["OPENAI_API_KEY", "OPENAI_BASE_URL"],
        "luna": ["LUNA_API_KEY", "LUNA_BASE_URL"],
        "terra": ["TERRA_API_KEY", "TERRA_BASE_URL"],
        "codestral": ["MISTRAL_API_KEY"],
        "mock": [],
    }.get(backend, [])
