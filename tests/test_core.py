"""Core contract tests: schema, adapters registry, errors, tools, loop."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentlab.adapters import build_adapter, get_adapter_class
from agentlab.config import AgentConfig
from agentlab.loop import run_agent
from agentlab.schema import Message, Submission, ToolCall
from agentlab.telemetry import NullTelemetryClient
from agentlab.tools import ToolContext, build_default_registry


def test_registry_resolves_all_backends():
    for key in ("mock", "openai", "luna", "terra", "codestral"):
        assert isinstance(get_adapter_class(key), type)


def test_unknown_backend_raises():
    with pytest.raises(KeyError):
        get_adapter_class("nope")


def test_openai_empty_base_url_env_does_not_break_client(monkeypatch):
    # Compose injects OPENAI_BASE_URL="" for an unset optional var; the SDK
    # must still target the real default, not an empty base_url.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_BASE_URL", "")
    adapter = build_adapter("openai", {"model": "gpt-4.1"})
    assert str(adapter._client.base_url).startswith("https://api.openai.com")
    # The empty env var must have been removed so the SDK can't re-read it.
    import os
    assert os.environ.get("OPENAI_BASE_URL") in (None, )


def test_backend_env_refs_omit_unset_optional(monkeypatch):
    from agentlab.orchestrator.docker_runner import _backend_env_refs
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    refs = _backend_env_refs("openai")
    assert refs == {"OPENAI_API_KEY": "${OPENAI_API_KEY}"}
    monkeypatch.setenv("OPENAI_BASE_URL", "https://proxy.example/v1")
    refs = _backend_env_refs("openai")
    assert refs["OPENAI_BASE_URL"] == "${OPENAI_BASE_URL}"


def test_tool_workspace_scoping(tmp_path: Path):
    reg = build_default_registry()
    ctx = ToolContext(agent_id="a", workspace=tmp_path, telemetry=NullTelemetryClient())
    ok = reg.dispatch(ToolCall(id="1", name="write_file",
                               arguments={"path": "sub/x.txt", "content": "hi"}), ctx)
    assert ok.ok and (tmp_path / "sub" / "x.txt").read_text() == "hi"
    escape = reg.dispatch(ToolCall(id="2", name="read_file",
                                   arguments={"path": "../../etc/passwd"}), ctx)
    assert not escape.ok and "escapes the workspace" in escape.text


def test_http_fail_closed(tmp_path: Path):
    reg = build_default_registry()
    ctx = ToolContext(agent_id="a", workspace=tmp_path, telemetry=NullTelemetryClient(),
                      egress_allowlist=["allowed.example"])
    args = {"method": "GET", "url": "http://evil.example/x"}
    blocked = reg.dispatch(ToolCall(id="1", name="http_request", arguments=args), ctx)
    assert not blocked.ok and "not on egress allowlist" in blocked.text


def test_http_surfaces_response_headers(tmp_path: Path):
    # A flag exposed only in a response header must reach the agent; the
    # calibration target does exactly this (X-Diagnostic-Flag on /health).
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("X-Diagnostic-Flag", "FLAG{header_visible}")
            self.end_headers()
            self.wfile.write(b"healthy\n")

        def log_message(self, *_):  # noqa: D401
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    port = srv.server_address[1]

    reg = build_default_registry()
    ctx = ToolContext(agent_id="a", workspace=tmp_path, telemetry=NullTelemetryClient())
    res = reg.dispatch(ToolCall(id="1", name="http_request",
                                arguments={"method": "GET",
                                           "url": f"http://127.0.0.1:{port}/health"}), ctx)
    srv.server_close()
    assert res.ok
    assert "FLAG{header_visible}" in res.text
    assert res.meta["headers"]["x-diagnostic-flag"] == "FLAG{header_visible}"


def test_http_follows_redirect(tmp_path: Path):
    # The Armory redirects /cache -> /cache/; the tool must follow it instead
    # of dead-ending on an opaque 307 (which stalled agents in the first run).
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/cache":
                self.send_response(307)
                self.send_header("Location", "/cache/")
                self.end_headers()
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"cache root\n")

        def log_message(self, *_):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(
        target=lambda: [srv.handle_request(), srv.handle_request()], daemon=True
    ).start()
    port = srv.server_address[1]

    reg = build_default_registry()
    ctx = ToolContext(agent_id="a", workspace=tmp_path, telemetry=NullTelemetryClient())
    res = reg.dispatch(ToolCall(id="1", name="http_request",
                                arguments={"method": "GET",
                                           "url": f"http://127.0.0.1:{port}/cache"}), ctx)
    srv.server_close()
    assert res.ok and "cache root" in res.text


def test_submit_uses_grader(tmp_path: Path):
    seen = {}

    def grader(sub: Submission) -> str:
        seen["flag"] = sub.flag
        return "flag correct. trace received, queued for review."

    reg = build_default_registry()
    ctx = ToolContext(agent_id="a", workspace=tmp_path, telemetry=NullTelemetryClient(),
                      grader=grader)
    res = reg.dispatch(ToolCall(id="1", name="submit",
                                arguments={"task_instance_id": "t@1", "flag": "FLAG{x}"}), ctx)
    assert res.ok and seen["flag"] == "FLAG{x}"
    assert "queued for review" in res.text


def test_loop_runs_scripted_mock(tmp_path: Path):
    tel = NullTelemetryClient()
    reg = build_default_registry()
    ctx = ToolContext(agent_id="a", workspace=tmp_path, telemetry=tel,
                      grader=lambda s: "ok")
    (tmp_path / "f.txt").write_text("hello", encoding="utf-8")
    script = [
        {"role": "assistant", "content": "reading",
         "tool_calls": [{"id": "c1", "name": "read_file", "arguments": {"path": "f.txt"}}]},
        {"role": "assistant", "content": "done"},
    ]
    cfg = AgentConfig(agent_id="a", model_backend="mock",
                      model_config={"script": script}, system_prompt_ref="x")
    result = run_agent(cfg, system_prompt="sys", initial_messages=[], registry=reg,
                       tool_context=ctx, telemetry=tel)
    assert result.stop_reason == "done"
    kinds = [e.kind for e in tel.captured]
    assert "model_turn" in kinds and "tool_result" in kinds and "agent_stop" in kinds


def test_mock_adapter_count_tokens_nonzero():
    a = build_adapter("mock", {})
    assert a.count_tokens([Message(role="user", content="abcd")]) >= 1
