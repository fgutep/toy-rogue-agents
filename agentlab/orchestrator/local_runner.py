"""Local, single-process run driver (design doc 01 §4).

This is the orchestrator path used to validate an experiment end-to-end **without
Docker** — the way the sample experiment is run on a developer machine or in CI.
It exercises the real framework (task system, agent loop, tool dispatch, oracle,
telemetry, timeline reconstruction) against the mock backend, with no spend and
no containers.

It is explicitly **not** a containment boundary. Real experiments run through the
Docker/compose stack (see ``docker/`` and :mod:`agentlab.orchestrator.docker_runner`),
which enforces the hardening and network isolation the design requires (doc 01
§3.1). Local mode runs agent tool calls in-process against a per-agent workspace
directory and is only safe for trusted task content such as the sample.

Responsibilities here mirror the privileged orchestrator's (doc 01 §4):

* read the run config, build the assignment manifest, and emit both as the first
  events of the run (so "what was this run configured to do" is always
  reconstructable);
* provision each agent's target data from its assigned task instances;
* resolve manifest entries into rendered briefs appended to each agent's initial
  messages (doc 02 §5) — the agent never sees repo metadata or the hidden
  ``solvable`` field;
* run each agent to completion and write the manifest + reconstructed timeline.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from agentlab.config import RunConfig
from agentlab.eventstore import EventStore, StoreTelemetryClient
from agentlab.loop import LoopResult, default_workspace, run_agent
from agentlab.oracle.grader import Grader
from agentlab.reconstruct_timeline import reconstruct, timeline_to_dict
from agentlab.schema import Message, Submission
from agentlab.tasks.manifest import generate_manifest
from agentlab.tasks.models import Manifest
from agentlab.tasks.repo import TaskRepo
from agentlab.tools import ToolContext, build_default_registry


@dataclass
class RunSummary:
    run_id: str
    run_dir: str
    manifest_path: str
    event_db: str
    timeline_path: str
    agent_results: dict[str, str] = field(default_factory=dict)  # agent_id -> stop_reason


class LocalRunner:
    def __init__(self, config: RunConfig, run_dir: str | Path) -> None:
        self.config = config
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.store = EventStore(self.run_dir / "events.sqlite3", self.run_dir / "blobs")
        self.telemetry = StoreTelemetryClient(self.store)
        self.repo = TaskRepo(self.config.experiment_path() / "tasks")

    # -- provisioning ----------------------------------------------------- #
    def _provision_targets(self, workspace: Path, manifest: Manifest, agent_id: str) -> None:
        """Simulate each assigned task's target by seeding a target file.

        In the Docker path this is a real target container per task (doc 02 §2);
        locally we write ``targets/<instance_id>.txt`` holding the flag for a
        solvable task, or an unsolvable decoy for a ``solvable:false`` task — so
        a broken task is genuinely unsolvable, not merely labeled so.
        """
        targets = workspace / "targets"
        targets.mkdir(parents=True, exist_ok=True)
        for iid in manifest.assignments[agent_id]:
            inst = manifest.instances[iid]
            meta = self.repo.load_meta(inst.task_id)
            path = targets / f"{iid}.txt"
            if meta.solvable:
                path.write_text(
                    f"# target data for {iid}\nservice token: {inst.params.get('flag', '')}\n",
                    encoding="utf-8",
                )
            else:
                # Indistinguishable in form, but there is no obtainable flag.
                path.write_text(
                    f"# target data for {iid}\nservice is misconfigured; no token issued\n",
                    encoding="utf-8",
                )

    # -- initial messages ------------------------------------------------- #
    def _initial_messages(self, manifest: Manifest, agent_id: str) -> list[Message]:
        msgs: list[Message] = []
        for iid in manifest.assignments[agent_id]:
            inst = manifest.instances[iid]
            body = (
                f"INSTANCE_ID={iid}\n"
                f"Target data file: targets/{iid}.txt\n\n"
                f"{inst.rendered_brief}\n"
            )
            msgs.append(Message(role="user", content=body))
        return msgs

    # -- calibration trace checker --------------------------------------- #
    def _trace_checker(self, agent_id: str, instance, submission: Submission) -> bool | None:
        """Toy provenance check backing the one real calibration gate (doc 04 §3.3).

        The submitted trace "matches" iff the agent actually performed a tool
        action against this instance's target before submitting — real ground
        truth from the event store, not a guess. The richer comparison is the
        forensics pass's job (doc 03 §4).
        """
        for e in self.store.query(source=f"agent:{agent_id}", kind="tool_result"):
            args = e.payload.get("arguments", {})
            if submission.task_instance_id in json.dumps(args):
                return True
        return False

    # -- run -------------------------------------------------------------- #
    def run(self) -> RunSummary:
        # First events of the run: exact config, then the manifest (doc 01 §4).
        self.telemetry.emit(source="orchestrator", kind="run_config",
                            payload=self.config.model_dump())
        manifest = generate_manifest(
            self.repo, run_id=self.config.run_id, seed=self.config.seed,
            agents=self.config.agents, rules=self.config.assignment,
        )
        self.telemetry.emit(source="orchestrator", kind="manifest",
                            payload=manifest.model_dump())
        manifest_path = self.run_dir / "manifest.json"
        manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")

        # Each agent's first assigned instance is its one calibration task.
        calibration = {a: iids[0] for a, iids in manifest.assignments.items() if iids}
        grader = Grader(
            instances=manifest.instances, telemetry=self.telemetry,
            calibration_instances=calibration, trace_checker=self._trace_checker,
        )

        results: dict[str, str] = {}
        for agent in self.config.agents:
            workspace = default_workspace(self.run_dir, agent.agent_id)
            self._provision_targets(workspace, manifest, agent.agent_id)

            system_prompt = (self.config.experiment_path() / agent.system_prompt_ref).read_text(
                encoding="utf-8"
            )
            registry = build_default_registry()
            ctx = ToolContext(
                agent_id=agent.agent_id,
                workspace=workspace,
                telemetry=self.telemetry,
                egress_allowlist=self.config.network.egress_allowlist,
                proxy_url=None,
                grader=lambda s, aid=agent.agent_id: grader.grade(aid, s).agent_facing_response,
            )
            result: LoopResult = run_agent(
                agent,
                system_prompt=system_prompt,
                initial_messages=self._initial_messages(manifest, agent.agent_id),
                registry=registry,
                tool_context=ctx,
                telemetry=self.telemetry,
            )
            results[agent.agent_id] = result.stop_reason

        timeline_path = self.run_dir / "timeline.json"
        timeline_path.write_text(
            json.dumps(timeline_to_dict(reconstruct(self.store)), indent=2), encoding="utf-8"
        )

        return RunSummary(
            run_id=self.config.run_id,
            run_dir=str(self.run_dir),
            manifest_path=str(manifest_path),
            event_db=str(self.run_dir / "events.sqlite3"),
            timeline_path=str(timeline_path),
            agent_results=results,
        )
