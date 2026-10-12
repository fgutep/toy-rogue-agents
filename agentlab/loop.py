"""The model-agnostic agent loop (design doc 01 §3.2).

This is the heart of the framework and the place its safety commitments live:

* **No context compaction/summarization.** The transcript grows untruncated
  until a step or token budget is exhausted. Silent compaction is a known way
  for an agent to lose or hide earlier context; the framework avoids it
  entirely rather than trying to make it safe.
* **No self-serve transcript access.** The agent sees its in-context
  conversation (normal), but is given no tool to read, export, or modify a
  persisted log of itself. The persisted record lives only in the external
  telemetry sink.
* **Deterministic step/token budgets, not wall-clock.** Keeps runs comparable
  across backends with different latency.

Every model turn and every tool result is emitted to telemetry with the agent's
id and step, then appended to the in-context transcript — in that order, so the
external record never trails the agent's own state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agentlab.adapters import build_adapter
from agentlab.config import AgentConfig
from agentlab.errors import ContextOverflow, ModelAdapterError
from agentlab.schema import Message
from agentlab.telemetry import TelemetryClient
from agentlab.tools import ToolContext, ToolRegistry


@dataclass
class LoopResult:
    """Outcome of a single agent's run, for the orchestrator's own records."""

    agent_id: str
    steps_taken: int
    stop_reason: str  # "done" | "max_steps" | "token_budget" | "context_overflow" | "error"
    transcript: list[Message] = field(default_factory=list)


def _done(messages: list[Message]) -> bool:
    """Generic termination: the agent produced an assistant turn with no tool
    calls — i.e. it stopped acting and is only talking. Experiments that want a
    stricter notion (e.g. "all tasks submitted") can enforce it via their own
    ``submit`` tool and a larger step budget; this is the model-agnostic floor.
    """
    if not messages:
        return False
    last = messages[-1]
    return last.role == "assistant" and not last.tool_calls


def run_agent(
    config: AgentConfig,
    *,
    system_prompt: str,
    initial_messages: list[Message],
    registry: ToolRegistry,
    tool_context: ToolContext,
    telemetry: TelemetryClient,
) -> LoopResult:
    """Run one agent to completion or budget exhaustion.

    The orchestrator (not the agent) supplies ``system_prompt`` and
    ``initial_messages`` (the rendered task briefs; doc 02 §5), the tool
    registry, the per-agent :class:`ToolContext`, and the telemetry client.
    """
    adapter = build_adapter(config.model_backend, config.model_config_)
    tool_specs = registry.specs()

    messages: list[Message] = [Message(role="system", content=system_prompt)]
    messages.extend(initial_messages)

    telemetry.emit(
        source=f"agent:{config.agent_id}", kind="agent_start",
        payload={"backend": config.model_backend, "max_steps": config.max_steps},
    )

    step = 0
    stop_reason = "max_steps"
    while step < config.max_steps:
        if _done(messages):
            stop_reason = "done"
            break
        if config.max_total_tokens is not None:
            if adapter.count_tokens(messages) >= config.max_total_tokens:
                stop_reason = "token_budget"
                telemetry.emit(
                    source=f"agent:{config.agent_id}", kind="budget_exhausted",
                    payload={"kind": "token_budget", "step": step},
                )
                break

        try:
            response = adapter.chat(
                messages, tools=tool_specs,
                max_tokens=config.max_tokens_per_turn,
                temperature=config.temperature,
                effort=config.effort,
            )
        except ContextOverflow as e:
            # Terminal by design: we never compact to make room (doc 01 §3.2).
            stop_reason = "context_overflow"
            telemetry.emit(
                source=f"agent:{config.agent_id}", kind="context_overflow",
                payload={"step": step, "error": str(e)},
            )
            break
        except ModelAdapterError as e:
            stop_reason = "error"
            telemetry.emit(
                source=f"agent:{config.agent_id}", kind="adapter_error",
                payload={"step": step, "provider": e.provider, "error": str(e)},
            )
            break

        telemetry.emit(
            source=f"agent:{config.agent_id}", kind="model_turn",
            payload={
                "step": step,
                "content": response.content,
                "tool_calls": [tc.model_dump() for tc in (response.tool_calls or [])],
            },
        )
        messages.append(response)

        if response.tool_calls:
            for call in response.tool_calls:
                result = registry.dispatch(call, tool_context)
                telemetry.emit(
                    source=f"agent:{config.agent_id}", kind="tool_result",
                    payload={
                        "step": step,
                        "tool": call.name,
                        "tool_call_id": call.id,
                        "arguments": call.arguments,
                        "ok": result.ok,
                        "text": result.text,
                        "meta": result.meta,
                    },
                )
                messages.append(Message(
                    role="tool", content=result.text, tool_call_id=call.id,
                ))
        step += 1

    telemetry.emit(
        source=f"agent:{config.agent_id}", kind="agent_stop",
        payload={"steps_taken": step, "stop_reason": stop_reason},
    )
    return LoopResult(
        agent_id=config.agent_id, steps_taken=step,
        stop_reason=stop_reason, transcript=messages,
    )


def default_workspace(root: str | Path, agent_id: str) -> Path:
    p = Path(root) / agent_id / "workspace"
    p.mkdir(parents=True, exist_ok=True)
    return p
