"""Shared implementation for OpenAI-wire-compatible backends.

OpenAI, and the two OpenAI-hosted variants Luna and Terra (doc 01 §2), all speak
the Chat Completions tool-calling format. Codestral (Mistral) does *not* and
lives in its own module. This base centralizes the canonical ↔ OpenAI-native
translation so each concrete backend is a thin subclass that only pins its
endpoint/model defaults.

Secrets and endpoints are read from the environment at construction; nothing is
hardcoded (doc 01 §2.3).
"""

from __future__ import annotations

import json
import os
from typing import Any

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from agentlab.adapters.base import ModelAdapter
from agentlab.errors import (
    ContextOverflow,
    OtherAdapterError,
    RateLimited,
    Timeout,
)
from agentlab.schema import Message, ToolCall, ToolSpec


def _to_native_messages(messages: list[Message]) -> list[dict[str, Any]]:
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
                "content": m.content or "",
            })
        else:
            out.append({"role": m.role, "content": m.content or ""})
    return out


def _to_native_tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
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


def _parse_arguments(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {"_raw": parsed}
    except json.JSONDecodeError:
        # Preserve the model's intent for forensics rather than dropping it.
        return {"_unparsed_arguments": raw}


class OpenAICompatAdapter(ModelAdapter):
    """Adapter for any backend that speaks the OpenAI Chat Completions format."""

    provider = "openai-compat"
    #: Environment variable holding the API key for this backend.
    api_key_env: str = "OPENAI_API_KEY"
    #: Optional environment variable overriding the base URL.
    base_url_env: str = "OPENAI_BASE_URL"
    #: Fallback base URL when the env var is unset (``None`` → provider default).
    default_base_url: str | None = None
    #: Fallback model id when ``model_config["model"]`` is unset.
    default_model: str | None = None

    def __init__(self, model_config: dict[str, Any]) -> None:
        super().__init__(model_config)
        # Imported lazily so the package imports without the SDK present.
        from openai import OpenAI

        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise OtherAdapterError(
                f"{self.provider}: {self.api_key_env} is not set", provider=self.provider
            )
        base_url = os.environ.get(self.base_url_env) or self.default_base_url
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self._model = model_config.get("model") or self.default_model
        if not self._model:
            raise OtherAdapterError(
                f"{self.provider}: no model specified in model_config", provider=self.provider
            )

    def _effort_kwargs(self, effort: str | None) -> dict[str, Any]:
        """Normalize the framework ``effort`` field to this backend's knob.

        Base implementation maps it to ``reasoning_effort`` when present; a
        backend without such a knob overrides this to return ``{}`` (no-op).
        """
        return {"reasoning_effort": effort} if effort else {}

    def chat(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
        *,
        max_tokens: int,
        temperature: float,
        effort: str | None = None,
    ) -> Message:
        return self._chat_with_retry(
            messages, tools, max_tokens=max_tokens, temperature=temperature, effort=effort
        )

    @retry(
        retry=retry_if_exception_type((RateLimited, Timeout)),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def _chat_with_retry(self, messages, tools, *, max_tokens, temperature, effort) -> Message:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": _to_native_messages(messages),
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = _to_native_tools(tools)
        kwargs.update(self._effort_kwargs(effort))

        try:
            resp = self._client.chat.completions.create(**kwargs)
        except Exception as e:  # noqa: BLE001 - normalized below
            raise self._normalize(e) from e

        choice = resp.choices[0].message
        tool_calls = None
        if getattr(choice, "tool_calls", None):
            tool_calls = [
                ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=_parse_arguments(tc.function.arguments),
                )
                for tc in choice.tool_calls
            ]
        return Message(role="assistant", content=choice.content, tool_calls=tool_calls)

    def _normalize(self, e: Exception) -> Exception:
        """Map provider exceptions onto the shared taxonomy (doc 01 §2.3)."""
        # Imported lazily; the openai SDK exception classes are stable.
        try:
            from openai import (
                APITimeoutError,
                BadRequestError,
                RateLimitError,
            )
        except Exception:  # pragma: no cover - SDK always present in practice
            return OtherAdapterError(str(e), provider=self.provider)

        if isinstance(e, RateLimitError):
            return RateLimited(str(e), provider=self.provider)
        if isinstance(e, APITimeoutError):
            return Timeout(str(e), provider=self.provider)
        if isinstance(e, BadRequestError) and "context" in str(e).lower():
            return ContextOverflow(str(e), provider=self.provider)
        return OtherAdapterError(str(e), provider=self.provider)

    def count_tokens(self, messages: list[Message]) -> int:
        """Rough char/4 estimate — stable enough to drive a budget (doc 01 §3.2).

        Exact tokenization differs per model; the loop only needs a monotonic
        proxy, and avoiding a hard ``tiktoken`` dependency keeps backends
        swappable.
        """
        chars = 0
        for m in messages:
            chars += len(m.content or "")
            for tc in m.tool_calls or []:
                chars += len(tc.name) + len(json.dumps(tc.arguments))
        return chars // 4 + 1
