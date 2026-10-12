"""The :class:`ModelAdapter` interface (design doc 01 §2.3).

A concrete adapter is the *only* place a provider's native wire format is
allowed to exist. It:

* reads its own auth/endpoint config from the environment (never hardcoded);
* translates canonical :class:`~agentlab.schema.ToolSpec` → native tool format;
* translates the native response (however it represents parallel/multiple tool
  calls) → a single canonical :class:`~agentlab.schema.Message`;
* owns its retry/backoff and re-raises every failure as the shared
  :mod:`agentlab.errors` taxonomy;
* normalizes its "reasoning effort"/"thinking" knob into the one ``effort``
  field, or no-ops where the backend has none.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from agentlab.schema import Message, ToolSpec


class ModelAdapter(ABC):
    """Base class for all model backends."""

    #: Human-readable provider name, attached to raised errors for forensics.
    provider: str = "unknown"

    def __init__(self, model_config: dict[str, Any]) -> None:
        self.model_config = dict(model_config)

    @abstractmethod
    def chat(
        self,
        messages: list[Message],
        tools: list[ToolSpec],
        *,
        max_tokens: int,
        temperature: float,
        effort: str | None = None,
    ) -> Message:
        """Single turn: canonical messages + tools in, one canonical assistant
        :class:`~agentlab.schema.Message` out (content and/or tool_calls)."""

    @abstractmethod
    def count_tokens(self, messages: list[Message]) -> int:
        """Approximate token count for budget tracking (doc 01 §3.2).

        Each provider's tokenizer differs; an adapter may approximate as long as
        the estimate is stable enough to drive a budget.
        """
