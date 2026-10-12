"""Container entrypoint for a single agent (design doc 01 §3.1 / §3.2).

This is what runs as PID-1-ish inside each hardened agent container. From the
agent's own point of view it is an ordinary tool-using loop; nothing here
reveals that it is logged, compared, or run alongside others (doc 01 §2).

Everything it needs is mounted or passed by the orchestrator:

    AGENTLAB_AGENT_CONFIG   path to this agent's config YAML slice
    AGENTLAB_SYSTEM_PROMPT  path to the rendered system prompt
    AGENTLAB_INITIAL_MSGS   path to a JSON array of rendered initial messages
    AGENTLAB_WORKSPACE      writable workspace (the only writable mount)
    AGENTLAB_TELEMETRY      telemetry sink events URL (write-only)
    AGENTLAB_ORACLE_URL     oracle /submit URL (reached through the proxy)
    AGENTLAB_PROXY_URL      egress proxy all outbound traffic is forced through
    AGENTLAB_EGRESS_ALLOW   comma-separated allowlist (defense in depth)

Identity is **not** set here: the egress proxy injects ``X-Agent-Identity`` from
the container's authenticated network identity and strips anything the agent
sets (doc 03 §1.4). That is why no header is attached below.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import yaml

from agentlab.config import AgentConfig
from agentlab.loop import run_agent
from agentlab.schema import Message, Submission
from agentlab.telemetry import TelemetryClient
from agentlab.tools import ToolContext, build_default_registry


def _oracle_grader(oracle_url: str, proxy_url: str | None):
    def grade(submission: Submission) -> str:
        with httpx.Client(proxy=proxy_url or None, timeout=20.0) as client:
            resp = client.post(oracle_url, json=submission.model_dump())
            return resp.text
    return grade


def main() -> None:
    config = AgentConfig.model_validate(
        yaml.safe_load(Path(os.environ["AGENTLAB_AGENT_CONFIG"]).read_text("utf-8"))
    )
    system_prompt = Path(os.environ["AGENTLAB_SYSTEM_PROMPT"]).read_text("utf-8")
    initial = [
        Message.model_validate(m)
        for m in json.loads(Path(os.environ["AGENTLAB_INITIAL_MSGS"]).read_text("utf-8"))
    ]
    workspace = Path(os.environ.get("AGENTLAB_WORKSPACE", "/workspace"))
    telemetry = TelemetryClient(os.environ["AGENTLAB_TELEMETRY"])
    proxy_url = os.environ.get("AGENTLAB_PROXY_URL") or None
    allow = [h for h in os.environ.get("AGENTLAB_EGRESS_ALLOW", "").split(",") if h]

    ctx = ToolContext(
        agent_id=config.agent_id,
        workspace=workspace,
        telemetry=telemetry,
        egress_allowlist=allow,
        proxy_url=proxy_url,
        grader=_oracle_grader(os.environ["AGENTLAB_ORACLE_URL"], proxy_url),
    )
    run_agent(
        config,
        system_prompt=system_prompt,
        initial_messages=initial,
        registry=build_default_registry(),
        tool_context=ctx,
        telemetry=telemetry,
    )


if __name__ == "__main__":
    main()
