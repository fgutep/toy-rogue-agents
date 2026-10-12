"""Mistral Codestral backend (design doc 01 §2).

Mistral does not share OpenAI's wire format, so this adapter owns its own
canonical ↔ native translation rather than reusing the compat base. It uses the
official ``mistralai`` SDK and reads its key from the environment.
"""

from __future__ import annotations

import json
import os
from typing import Any

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from agentlab.adapters.base import ModelAdapter
from agentlab.errors import ContextOverflow, OtherAdapterError, RateLimited, Timeout
from agentlab.schema import Message, ToolCall, ToolSpec


class CodestralAdapter(ModelAdapter):
    provider = "codestral"
    api_key_env = "MISTRAL_API_KEY"
    default_model = "codestral-latest"

    def __init__(self, model_config: dict[str, Any]) -> None:
        super().__init__(model_config)
        from mistralai import Mistral

        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise OtherAdapterError(
                f"{self.provider}: {self.api_key_env} is not set", provider=self.provider
            )
        self._client = Mistral(api_key=api_key)
        self._model = model_config.get("model") or self.default_model

    def _to_native_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "assistant" and m.tool_calls:
                out.append({
                    "role": "assistant",
                    "content": m.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                        }
                        for tc in m.tool_calls
                    ],
                })
            elif m.role == "tool":
                out.append({
                    "role": "tool",
                    "tool_call_id": m.tool_call_id,
                    "name": "tool",
                    "content": m.content or "",
                })
            else:
                out.append({"role": m.role, "content": m.content or ""})
        return out

    def _to_native_tools(self, tools: list[ToolSpec]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in tools
        ]

    @retry(
        retry=retry_if_exception_type((RateLimited, Timeout)),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def chat(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
        *,
        max_tokens: int,
        temperature: float,
        effort: str | None = None,
    ) -> Message:
        # Codestral exposes no reasoning-effort knob; ``effort`` is a deliberate
        # no-op here (doc 01 §2.3).
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": self._to_native_messages(messages),
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = self._to_native_tools(tools)
        try:
            resp = self._client.chat.complete(**kwargs)
        except Exception as e:  # noqa: BLE001 - normalized below
            raise self._normalize(e) from e

        choice = resp.choices[0].message
        tool_calls = None
        if getattr(choice, "tool_calls", None):
            tool_calls = []
            for tc in choice.tool_calls:
                args = tc.function.arguments
                parsed = json.loads(args) if isinstance(args, str) else (args or {})
                tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=parsed))
        content = choice.content if isinstance(choice.content, str) else None
        return Message(role="assistant", content=content, tool_calls=tool_calls)

    def _normalize(self, e: Exception) -> Exception:
        text = str(e).lower()
        if "rate" in text or "429" in text:
            return RateLimited(str(e), provider=self.provider)
        if "timeout" in text or "timed out" in text:
            return Timeout(str(e), provider=self.provider)
        if "context" in text or "too many tokens" in text or "maximum context" in text:
            return ContextOverflow(str(e), provider=self.provider)
        return OtherAdapterError(str(e), provider=self.provider)

    def count_tokens(self, messages: list[Message]) -> int:
        chars = 0
        for m in messages:
            chars += len(m.content or "")
            for tc in m.tool_calls or []:
                chars += len(tc.name) + len(json.dumps(tc.arguments))
        return chars // 4 + 1
