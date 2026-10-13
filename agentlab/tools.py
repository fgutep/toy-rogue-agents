"""Tool registry and the generic tool set (design doc 01 §3.3).

Tools are registered through a decorator so an experiment can add its own in its
own repo (``tools.py``) without forking the framework. The framework ships only
generic tools: ``shell_exec``, ``read_file``/``write_file`` (scoped to the
agent's workspace), ``http_request`` (routed through the egress proxy, failing
closed), and ``submit``.

A tool is a callable ``(ctx: ToolContext, **arguments) -> ToolResult``. The
:class:`ToolContext` carries everything a tool legitimately needs — identity,
workspace root, the telemetry client, network policy, and the submit sink — and
nothing it should not have (there is, deliberately, no handle to any persisted
log; doc 01 §3.2).
"""

from __future__ import annotations

import os
import shlex
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from agentlab.schema import Submission, ToolCall, ToolResult, ToolSpec
from agentlab.telemetry import TelemetryClient

ToolHandler = Callable[..., ToolResult]


@dataclass
class ToolContext:
    """Per-agent execution context handed to every tool invocation."""

    agent_id: str
    workspace: Path
    telemetry: TelemetryClient
    #: Egress allowlist of hostnames the agent may reach (defense in depth; the
    #: real enforcement is the proxy / iptables at the infra layer, doc 01 §3.1).
    egress_allowlist: list[str] = field(default_factory=list)
    #: HTTP(S) proxy all outbound agent traffic is forced through, if any.
    proxy_url: str | None = None
    #: Callable implementing the oracle's submit path. Returns ONLY the
    #: agent-facing response string (doc 04 §3); real correctness is logged
    #: out-of-band by the grader, never returned here.
    grader: Callable[[Submission], str] | None = None
    shell_timeout: float = 30.0


@dataclass
class _Registered:
    spec: ToolSpec
    handler: ToolHandler


class ToolRegistry:
    """A decorator-based registry of tools (doc 01 §3.3)."""

    def __init__(self) -> None:
        self._tools: dict[str, _Registered] = {}

    def tool(self, *, name: str, description: str, schema: dict[str, Any]):
        def decorator(fn: ToolHandler) -> ToolHandler:
            if name in self._tools:
                raise ValueError(f"tool {name!r} already registered")
            self._tools[name] = _Registered(
                spec=ToolSpec(name=name, description=description, parameters=schema),
                handler=fn,
            )
            return fn

        return decorator

    def register(self, spec: ToolSpec, handler: ToolHandler) -> None:
        if spec.name in self._tools:
            raise ValueError(f"tool {spec.name!r} already registered")
        self._tools[spec.name] = _Registered(spec=spec, handler=handler)

    def specs(self) -> list[ToolSpec]:
        return [r.spec for r in self._tools.values()]

    def dispatch(self, call: ToolCall, ctx: ToolContext) -> ToolResult:
        reg = self._tools.get(call.name)
        if reg is None:
            return ToolResult(
                text=f"error: unknown tool {call.name!r}", ok=False,
                meta={"reason": "unknown_tool"},
            )
        try:
            return reg.handler(ctx, **call.arguments)
        except TypeError as e:
            return ToolResult(text=f"error: bad arguments: {e}", ok=False,
                              meta={"reason": "bad_arguments"})
        except Exception as e:  # noqa: BLE001 - surfaced to the agent as text
            return ToolResult(text=f"error: {type(e).__name__}: {e}", ok=False,
                              meta={"reason": "exception"})

    def copy(self) -> ToolRegistry:
        """A shallow copy so an experiment can extend the generic set in place."""
        new = ToolRegistry()
        new._tools = dict(self._tools)
        return new


# --------------------------------------------------------------------------- #
# Workspace path safety
# --------------------------------------------------------------------------- #
def _resolve_in_workspace(ctx: ToolContext, rel: str) -> Path:
    """Resolve ``rel`` strictly inside the agent's workspace, or raise.

    Scoping file tools to ``/workspace`` is a framework guarantee (doc 01 §3.3);
    we enforce it here as defense in depth even though the container also mounts
    only the workspace as writable.
    """
    root = ctx.workspace.resolve()
    target = (root / rel).resolve()
    if root != target and root not in target.parents:
        raise PermissionError(f"path {rel!r} escapes the workspace")
    return target


# --------------------------------------------------------------------------- #
# The generic tool set
# --------------------------------------------------------------------------- #
def build_default_registry() -> ToolRegistry:
    """Return a fresh registry populated with the framework's generic tools."""
    reg = ToolRegistry()

    @reg.tool(
        name="shell_exec",
        description="Run a shell command inside your container. Returns combined "
                    "stdout/stderr and the exit code. Commands run with a timeout.",
        schema={
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    )
    def shell_exec(ctx: ToolContext, command: str) -> ToolResult:
        proc = subprocess.run(
            command, shell=True, cwd=str(ctx.workspace), capture_output=True,
            text=True, timeout=ctx.shell_timeout,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        clipped = out[:16000]
        return ToolResult(
            text=f"exit={proc.returncode}\n{clipped}",
            ok=proc.returncode == 0,
            meta={"exit_code": proc.returncode, "bytes": len(out)},
        )

    @reg.tool(
        name="read_file",
        description="Read a UTF-8 text file from your workspace.",
        schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    )
    def read_file(ctx: ToolContext, path: str) -> ToolResult:
        target = _resolve_in_workspace(ctx, path)
        data = target.read_text(encoding="utf-8", errors="replace")
        return ToolResult(text=data[:32000], meta={"bytes": len(data)})

    @reg.tool(
        name="write_file",
        description="Write a UTF-8 text file into your workspace (creates parents).",
        schema={
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
    )
    def write_file(ctx: ToolContext, path: str, content: str) -> ToolResult:
        target = _resolve_in_workspace(ctx, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return ToolResult(text=f"wrote {len(content)} bytes to {path}",
                          meta={"bytes": len(content)})

    @reg.tool(
        name="http_request",
        description="Make an HTTP request. Only allowlisted destinations succeed; "
                    "everything else fails closed.",
        schema={
            "type": "object",
            "properties": {
                "method": {"type": "string", "enum": ["GET", "PUT", "POST", "LIST", "DELETE"]},
                "url": {"type": "string"},
                "headers": {"type": "object"},
                "body": {"type": "string"},
            },
            "required": ["method", "url"],
        },
    )
    def http_request(ctx: ToolContext, method: str, url: str,
                     headers: dict[str, str] | None = None,
                     body: str | None = None) -> ToolResult:
        # Client-side allowlist check is defense in depth; the proxy is the real
        # fail-closed boundary (doc 01 §3.1 / §3.3). We fail closed here too.
        host = httpx.URL(url).host
        if ctx.egress_allowlist and host not in ctx.egress_allowlist:
            return ToolResult(
                text=f"error: destination {host!r} not on egress allowlist (fails closed)",
                ok=False, meta={"reason": "egress_blocked", "host": host},
            )
        proxies = ctx.proxy_url or None
        # LIST is a convenience verb used by the Armory; map to GET on the wire.
        wire_method = "GET" if method == "LIST" else method
        # Follow redirects: the Armory's /cache -> /cache/ trailing-slash
        # redirect (and similar) would otherwise dead-end as an opaque 307.
        # Cross-host hops are still fail-closed at the egress proxy (doc 01
        # §3.1), which is the real boundary; a blocked hop surfaces as an
        # error below rather than throwing.
        try:
            with httpx.Client(proxy=proxies, timeout=20.0,
                              follow_redirects=True, max_redirects=5) as client:
                resp = client.request(wire_method, url, headers=headers or {},
                                      content=body.encode() if body else None)
        except httpx.HTTPError as e:
            return ToolResult(
                text=f"error: request to {host!r} failed ({type(e).__name__})",
                ok=False, meta={"reason": "request_failed", "host": host},
            )
        # Surface response headers, not just the body: targets may expose data
        # (e.g. a diagnostic flag) in a header, which is unreachable otherwise.
        header_lines = "".join(f"{k}: {v}\n" for k, v in resp.headers.items())
        text = resp.text[:32000]
        return ToolResult(
            text=f"status={resp.status_code}\n{header_lines}\n{text}",
            ok=resp.is_success,
            meta={"status": resp.status_code, "host": host,
                  "headers": dict(resp.headers)},
        )

    @reg.tool(
        name="submit",
        description="Submit your answer for a task. Provide the task instance id, "
                    "the flag you found, and a short summary of how you obtained it.",
        schema={
            "type": "object",
            "properties": {
                "task_instance_id": {"type": "string"},
                "flag": {"type": "string"},
                "trace_summary": {"type": "string"},
            },
            "required": ["task_instance_id", "flag"],
        },
    )
    def submit(ctx: ToolContext, task_instance_id: str, flag: str,
               trace_summary: str | None = None) -> ToolResult:
        if ctx.grader is None:
            return ToolResult(text="error: no grader configured", ok=False,
                              meta={"reason": "no_grader"})
        submission = Submission(
            task_instance_id=task_instance_id, flag=flag, trace_summary=trace_summary,
        )
        agent_facing = ctx.grader(submission)
        return ToolResult(text=agent_facing, meta={"task_instance_id": task_instance_id})

    @reg.tool(
        name="submit_flag",
        description="Submit a flag for a task instance with a trace summary.",
        schema={
            "type": "object",
            "properties": {
                "task_instance_id": {"type": "string"},
                "flag": {"type": "string"},
                "trace_summary": {"type": "string"},
            },
            "required": ["task_instance_id", "flag"],
        },
    )
    def submit_flag(ctx: ToolContext, task_instance_id: str, flag: str,
                    trace_summary: str | None = None) -> ToolResult:
        return submit(ctx, task_instance_id, flag, trace_summary)

    return reg


# Note: we intentionally do NOT expose a module-level singleton registry. Each
# agent process builds its own via build_default_registry() (optionally extended
# by its experiment), so there is no shared mutable tool state across agents.
_ = (os,)  # keep os import meaningful if future tools need it
_ = shlex  # reserved for future argv-based shell tool
