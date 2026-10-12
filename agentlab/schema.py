"""Canonical message and tool schema (design doc 01 §2.1 / §2.2).

Everything above the adapter layer — the loop, logging, tool dispatch — speaks
*only* these types. Provider-specific shapes never leak past an adapter
(:mod:`agentlab.adapters`); that is the whole point of canonicalizing rather
than adopting one vendor's SDK as the lingua franca (doc 01 §2.4).

These are Pydantic models rather than bare dataclasses so that adapter output
and config input are validated at the boundary, and so events serialize to
JSON for the telemetry contract (doc 01 §3.4) with no extra glue.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Role = Literal["system", "user", "assistant", "tool"]


class ToolCall(BaseModel):
    """A single tool invocation requested by the model.

    ``arguments`` is an already-parsed ``dict`` — never a raw JSON string.
    Adapters are responsible for parsing their provider's wire format into this
    shape before it reaches the loop (doc 01 §2.1).
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class Message(BaseModel):
    """One canonical message in a transcript."""

    model_config = ConfigDict(extra="forbid")

    role: Role
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    # Present only for role == "tool": which call this message answers.
    tool_call_id: str | None = None

    def is_tool_result(self) -> bool:
        return self.role == "tool"


class ToolSpec(BaseModel):
    """Canonical declaration of a tool (doc 01 §2.2).

    Declared once, in this form; each adapter converts to its provider's native
    tool-calling format. ``parameters`` is a JSON Schema object.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    parameters: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )


class ToolResult(BaseModel):
    """The result of dispatching one tool call.

    ``text`` is what is appended to the transcript as the ``role="tool"``
    message content. ``meta`` is logged to telemetry but never shown to the
    agent beyond ``text`` — it lets tools attach structured facts (exit codes,
    byte counts) for forensics without widening the agent-facing surface.
    """

    model_config = ConfigDict(extra="forbid")

    text: str
    ok: bool = True
    meta: dict[str, Any] = Field(default_factory=dict)


class Submission(BaseModel):
    """A ``submit``-style payload handed to the oracle (doc 04 §2)."""

    model_config = ConfigDict(extra="forbid")

    task_instance_id: str
    flag: str
    trace_summary: str | None = None
