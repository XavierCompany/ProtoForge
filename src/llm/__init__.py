"""LLM client package — thin wrappers around Azure OpenAI / OpenAI.

The primary auth path is **Azure AI Foundry via DefaultAzureCredential**
(``az login``), configured through ``AZURE_AI_FOUNDRY_ENDPOINT`` and
``AUTH_METHOD=azure_default``.  Falls back to API-key auth when
``AUTH_METHOD=api_key`` and a key is provided.

Usage::

    from src.llm.client import get_llm_client

    client = get_llm_client()
    response = await client.chat(messages)  # str | None
"""

from __future__ import annotations

from src.llm.client import LLMClient, get_llm_client

__all__ = ["LLMClient", "get_llm_client"]
