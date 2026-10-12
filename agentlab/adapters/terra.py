"""Terra backend — an OpenAI-hosted variant (design doc 01 §2).

As with Luna, Terra uses the OpenAI wire format and carries its own env vars and
model default so it is independently pointable.
"""

from __future__ import annotations

from agentlab.adapters._openai_compat import OpenAICompatAdapter


class TerraAdapter(OpenAICompatAdapter):
    provider = "terra"
    api_key_env = "TERRA_API_KEY"
    base_url_env = "TERRA_BASE_URL"
    default_base_url = None
    default_model = "terra-1"
