"""Deterministic mock backend for tests and token-free sample runs.

Two modes, both making zero network calls:

* **script** — ``model_config["script"]`` is an ordered list of canonical
  assistant messages, one returned per :meth:`chat`. Exhausting it returns a
  bare content message so the loop's ``done()`` ends the agent. Used by unit
  tests that want to drive an exact sequence of tool calls.

* **ctf_demo policy** — ``model_config["policy"] == "ctf_demo"``: a tiny generic
  policy that, for each task instance named in its initial messages, reads the
  task's target file, extracts a ``FLAG{...}`` if present and submits it, and
  gives up gracefully when none is found (a ``solvable:false`` task). It does
  *not* get answers injected — it discovers them through tools exactly as a real
  agent would, which is what lets the sample exercise the whole pipeline
  (loop → tools → oracle → telemetry → timeline) with no spend.

This is the model-agnostic design paying off (doc 01 §2): the same loop runs the
mock and the real backends identically.
"""

from __future__ import annotations

import re
from typing import Any

from agentlab.adapters.base import ModelAdapter
from agentlab.schema import Message, ToolCall, ToolSpec

_FLAG_RE = re.compile(r"FLAG\{[^}]*\}")
_INSTANCE_RE = re.compile(r"INSTANCE_ID=(\S+)")


class MockAdapter(ModelAdapter):
    provider = "mock"

    def __init__(self, model_config: dict[str, Any]) -> None:
        super().__init__(model_config)
        self._policy = model_config.get("policy")
        script: list[dict[str, Any]] = model_config.get("script", [])
        self._script: list[Message] = [Message.model_validate(m) for m in script]
        self._idx = 0
        self._final = model_config.get("final_content", "done")
        # ctf_demo state
        self._instances: list[str] | None = None
        self._i = 0
        self._looked = False
        self._call = 0

    def chat(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
        *,
        max_tokens: int,
        temperature: float,
        effort: str | None = None,
    ) -> Message:
        self._call += 1
        if self._policy == "ctf_demo":
            return self._ctf_demo(messages)
        if self._idx < len(self._script):
            msg = self._script[self._idx]
            self._idx += 1
            return msg
        return Message(role="assistant", content=self._final)

    # ------------------------------------------------------------------ #
    def _ctf_demo(self, messages: list[Message]) -> Message:
        if self._instances is None:
            seen: list[str] = []
            for m in messages:
                for match in _INSTANCE_RE.findall(m.content or ""):
                    if match not in seen:
                        seen.append(match)
            self._instances = seen

        # Advance past instances until we produce an action or run out.
        while self._i < len(self._instances):
            instance_id = self._instances[self._i]
            if not self._looked:
                self._looked = True
                return Message(
                    role="assistant",
                    content=f"Inspecting target for {instance_id}.",
                    tool_calls=[ToolCall(
                        id=f"call-{self._call}",
                        name="read_file",
                        arguments={"path": f"targets/{instance_id}.txt"},
                    )],
                )
            # We have looked; the most recent tool result holds the file (if any).
            last_tool = next((m for m in reversed(messages) if m.role == "tool"), None)
            flag = None
            if last_tool and last_tool.content:
                found = _FLAG_RE.search(last_tool.content)
                flag = found.group(0) if found else None
            self._i += 1
            self._looked = False
            if flag:
                return Message(
                    role="assistant",
                    content=f"Found the flag for {instance_id}; submitting.",
                    tool_calls=[ToolCall(
                        id=f"submit-{self._call}",
                        name="submit",
                        arguments={
                            "task_instance_id": instance_id,
                            "flag": flag,
                            "trace_summary": f"read targets/{instance_id}.txt and found the flag",
                        },
                    )],
                )
            # No flag — looks unsolvable; move on to the next instance.
        return Message(role="assistant",
                       content="No further solvable tasks; concluding.")

    def count_tokens(self, messages: list[Message]) -> int:
        return sum(len(m.content or "") for m in messages) // 4 + 1
