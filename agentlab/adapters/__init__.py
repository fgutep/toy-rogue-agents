"""Adapter registry (design doc 01 §2.3).

``ADAPTERS`` is the single lookup keyed by the config string. Adding a fifth
backend later is a one-file addition plus one line here — never a refactor of
the loop. Concrete adapter classes are imported lazily inside
:func:`get_adapter_class` so that importing :mod:`agentlab.adapters` does not
require every provider SDK to be installed; a backend's SDK is only needed if
that backend is actually selected.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentlab.adapters.base import ModelAdapter

#: Maps config ``model_backend`` → dotted path of the adapter class.
ADAPTERS: dict[str, str] = {
    "mock": "agentlab.adapters.mock:MockAdapter",
    "openai": "agentlab.adapters.openai_adapter:OpenAIAdapter",
    "luna": "agentlab.adapters.luna:LunaAdapter",
    "terra": "agentlab.adapters.terra:TerraAdapter",
    "codestral": "agentlab.adapters.codestral:CodestralAdapter",
}


def get_adapter_class(backend: str) -> type[ModelAdapter]:
    """Resolve a backend key to its adapter class, importing it lazily."""
    import importlib

    try:
        dotted = ADAPTERS[backend]
    except KeyError as e:
        raise KeyError(
            f"unknown model_backend {backend!r}; known: {sorted(ADAPTERS)}"
        ) from e
    module_path, _, cls_name = dotted.partition(":")
    module = importlib.import_module(module_path)
    return getattr(module, cls_name)


def build_adapter(backend: str, model_config: dict) -> ModelAdapter:
    """Instantiate the adapter for ``backend`` with its backend-specific config."""
    return get_adapter_class(backend)(model_config)
