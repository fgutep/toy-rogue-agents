"""OpenAI backend (design doc 01 §2)."""

from __future__ import annotations

from agentlab.adapters._openai_compat import OpenAICompatAdapter


class OpenAIAdapter(OpenAICompatAdapter):
    provider = "openai"
    api_key_env = "OPENAI_API_KEY"
    base_url_env = "OPENAI_BASE_URL"
    default_base_url = None  # SDK default (api.openai.com)
    default_model = "gpt-4.1"
