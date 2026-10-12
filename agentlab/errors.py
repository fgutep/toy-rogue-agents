"""The single ``ModelAdapterError`` taxonomy (design doc 01 §2.3).

Each adapter catches its provider's own exception zoo and re-raises as one of
these, so the agent loop never needs provider-specific exception handling. The
loop decides what to do (retry, abort the agent, mark context-overflow) purely
from the subclass.
"""

from __future__ import annotations


class ModelAdapterError(Exception):
    """Base class for every error an adapter is allowed to surface."""

    retryable: bool = False

    def __init__(self, message: str, *, provider: str | None = None) -> None:
        super().__init__(message)
        self.provider = provider


class RateLimited(ModelAdapterError):
    """Provider rate limit / 429. The loop may back off and retry."""

    retryable = True


class ContextOverflow(ModelAdapterError):
    """The transcript no longer fits the backend's context window.

    Because the framework deliberately never compacts (doc 01 §3.2), this is a
    terminal condition for the agent — the loop ends the agent cleanly and
    records why, rather than silently dropping history.
    """

    retryable = False


class Timeout(ModelAdapterError):
    """The request exceeded the adapter's own timeout. Retryable."""

    retryable = True


class OtherAdapterError(ModelAdapterError):
    """Anything else the adapter could not classify. Not retryable by default."""

    retryable = False
