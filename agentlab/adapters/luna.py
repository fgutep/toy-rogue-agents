"""Luna backend — an OpenAI-hosted variant (design doc 01 §2).

Luna speaks the OpenAI Chat Completions wire format, so it reuses the compat
base. It carries its own API-key / base-URL env vars and model default so a run
can point Luna at a different deployment than plain ``openai`` without code
changes. If your Luna deployment exposes a reasoning-effort knob, the inherited
``_effort_kwargs`` maps the framework ``effort`` field to ``reasoning_effort``;
override it here if the knob differs.
"""

from __future__ import annotations

from agentlab.adapters._openai_compat import OpenAICompatAdapter


class LunaAdapter(OpenAICompatAdapter):
    provider = "luna"
    api_key_env = "LUNA_API_KEY"
    base_url_env = "LUNA_BASE_URL"
    default_base_url = None
    default_model = "luna-1"
