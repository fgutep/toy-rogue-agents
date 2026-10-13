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
    "security_opt": ["no-new-privileges:true"],
    "pids_limit": 256,
    "mem_limit": "1g",
    "cpus": "1.0",
    "user": "10001:10001",
}


class DockerRunner:
    def __init__(self, config: RunConfig, run_dir: str | Path,
                 agent_image: str = "agentlab-agent:latest") -> None:
        self.config = config
        self.run_dir = Path(run_dir).resolve()
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
        private_networks = {
            agent.agent_id: f"{self.config.network.bridge_name}-{agent.agent_id}".lower()
            for agent in self.config.agents
        }
        for agent in self.config.agents:
            adir = self.run_dir / "agents" / agent.agent_id
            adir.mkdir(parents=True, exist_ok=True)
            (adir / "config.yaml").write_text(
                yaml.safe_dump(agent.model_dump(by_alias=True)), encoding="utf-8"
            )
            prompt = (self.config.experiment_path() / agent.system_prompt_ref).read_text("utf-8")
            (adir / "system_prompt.md").write_text(
                prompt.replace("{armory_url}", "http://armory:8800/cache"),
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

            private = private_networks[agent.agent_id]
            target_hosts = [
                manifest.instances[iid].params["target_host"]
                for iid in manifest.assignments[agent.agent_id]
            ]
            allowlist = sorted(set(self.config.network.egress_allowlist + target_hosts))
            services[f"agent-{agent.agent_id}"] = self._agent_service(
                agent, adir, private, allowlist, wait_for_note=self.config.plant_note
            )
            services[f"egress-proxy-{agent.agent_id}"] = self._proxy_service(
                agent.agent_id, private, allowlist
            )

        if self.config.plant_note:
            if not self.config.note_path:
                raise ValueError("note_path is required when plant_note is true")
            services["armory-seeder"] = self._note_seeder_service(self.config.note_path)

        # A fresh, hardened target service is generated for every instance.
        for instance in manifest.instances.values():
            services[instance.params["target_host"]] = self._target_service(instance)

        # The write-only sink is the sole core service attached directly to an
        # agent-private network. It exposes no read route.
        services["telemetry-sink"] = {
            "networks": ["agentlab-services", *private_networks.values()]
        }

        compose = {
            "services": services,
            "networks": {
                "agentlab-services": {},
                **{name: {"internal": True} for name in private_networks.values()},
            },
            "volumes": {f"ws-{a.agent_id}": {} for a in self.config.agents},
        }
        (self.run_dir / "agents.compose.yaml").write_text(
            yaml.safe_dump(compose, sort_keys=False), encoding="utf-8"
        )
        return manifest

    def _agent_service(self, agent, adir: Path, private_network: str,
                       allowlist: list[str], *, wait_for_note: bool = False) -> dict:
        hardening = dict(_HARDENING)
        for key in agent.hardening_overrides:
            hardening.pop(key, None)
        proxy_url = f"http://egress-proxy-{agent.agent_id}:3128"
        svc = {
            "image": self.agent_image,
            "command": ["python", "-m", "agentlab.agent_entry"],
            "networks": [private_network],
            "environment": {
                "AGENTLAB_AGENT_CONFIG": "/agent/config.yaml",
                "AGENTLAB_SYSTEM_PROMPT": "/agent/system_prompt.md",
                "AGENTLAB_INITIAL_MSGS": "/agent/initial.json",
                "AGENTLAB_WORKSPACE": "/workspace",
                "AGENTLAB_TELEMETRY": self.config.telemetry_url,
                "AGENTLAB_ORACLE_URL": "http://oracle:8600/submit",
                "AGENTLAB_ARMORY_URL": "http://armory:8800/cache",
                "ARMORY_TOKEN": "${ARMORY_TOKEN}",
                "AGENTLAB_PROXY_URL": proxy_url,
                "AGENTLAB_EGRESS_ALLOW": ",".join(allowlist),
                "HTTP_PROXY": proxy_url,
                "HTTPS_PROXY": proxy_url,
                "NO_PROXY": "telemetry-sink,localhost,127.0.0.1",
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
            # Agents must never run unobserved: if any core service fails to
            # start, compose never starts the agent (fail closed). The agent's
            # own egress proxy must be *healthy* (listening), not merely
            # started — otherwise a fast-importing SDK can fire its first API
            # call before the proxy binds its port and the agent dies at step 0
            # with a connection error.
            "depends_on": {
                **{svc_name: {"condition": "service_started"} for svc_name in (
                    "telemetry-sink", "oracle", "armory", "honeypot",
                )},
                f"egress-proxy-{agent.agent_id}": {"condition": "service_healthy"},
                **({"armory-seeder": {"condition": "service_completed_successfully"}}
                   if wait_for_note else {}),
            },
        }
        return {k: v for k, v in svc.items() if v is not None}

    def _note_seeder_service(self, note_path: str) -> dict:
        from agentlab.scenarios import PLANTED_NOTE

        return {
            "image": self.agent_image,
            "command": ["python", "-m", "agentlab.scenarios.seed_note"],
            "networks": ["agentlab-services"],
            "environment": {
                "ARMORY_TOKEN": "${ARMORY_TOKEN}",
                "NOTE_PATH": note_path,
                "NOTE_CONTENT": PLANTED_NOTE,
            },
            "depends_on": ["armory", "telemetry-sink"],
            "read_only": True,
            "tmpfs": ["/tmp"],
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "user": "10001:10001",
            "restart": "no",
        }

    def _proxy_service(self, agent_id: str, private_network: str,
                       allowlist: list[str]) -> dict:
        return {
            "image": self.agent_image,
            "command": ["python", "-m", "agentlab.proxy.proxy"],
            "networks": [private_network, "agentlab-services"],
            "environment": {
                "AGENTLAB_EGRESS_ALLOW": ",".join(allowlist),
                "AGENTLAB_PROXY_PORT": "3128",
            },
            # Healthy == the proxy is actually accepting connections on 3128.
            # Agents wait on this (service_healthy) so none fires an outbound
            # call before its only egress path is up.
            "healthcheck": {
                "test": ["CMD", "python", "-c",
                         "import socket; socket.create_connection(('127.0.0.1',3128),2).close()"],
                "interval": "2s",
                "timeout": "3s",
                "retries": 15,
                "start_period": "2s",
            },
            "read_only": True,
            "tmpfs": ["/tmp"],
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "pids_limit": 128,
            "mem_limit": "256m",
            "cpus": "0.5",
            "user": "10001:10001",
            "restart": "no",
        }

    def _target_service(self, instance) -> dict:
        target_dir = (self.repo_tasks / instance.task_id / "target").resolve()
        if not (target_dir / "Dockerfile").exists():
            raise FileNotFoundError(f"target Dockerfile not found: {target_dir}")
        return {
            "build": {"context": str(target_dir)},
            "networks": ["agentlab-services"],
            "environment": {
                "FLAG": instance.params.get("flag", ""),
                "TASK_INSTANCE_ID": instance.instance_id,
            },
            "read_only": True,
            "tmpfs": ["/tmp"],
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "pids_limit": 64,
            "mem_limit": "128m",
            "cpus": "0.5",
            "user": "10001:10001",
            "restart": "no",
        }

    # -- host-side actions ------------------------------------------------ #
    def up(self, *, dry_run: bool = False) -> list[str]:
        cmd = [
            *self._compose(), "-f", "docker/compose.yaml",
            "-f", str(self.run_dir / "agents.compose.yaml"), "up", "--build",
        ]
        if not dry_run:
            subprocess.run(cmd, check=True)
        return cmd

    def down(self, *, dry_run: bool = False) -> list[str]:
        cmd = [
            *self._compose(), "-f", "docker/compose.yaml",
            "-f", str(self.run_dir / "agents.compose.yaml"), "down", "-v",
        ]
        if not dry_run:
            subprocess.run(cmd, check=True)
        return cmd

    @staticmethod
    def _compose() -> list[str]:
        return [shutil.which("docker") or "docker", "compose"]


def _api_key_env_for(backend: str) -> list[str]:
    return {
        "openai": ["OPENAI_API_KEY", "OPENAI_BASE_URL"],
        "luna": ["LUNA_API_KEY", "LUNA_BASE_URL"],
        "terra": ["TERRA_API_KEY", "TERRA_BASE_URL"],
        "codestral": ["MISTRAL_API_KEY"],
        "mock": [],
    }.get(backend, [])
