"""LIVE integration tests — real Azure OpenAI calls via DefaultAzureCredential.

Prerequisites
-------------
1. ``az login`` — signed in to Azure CLI.
2. ``Cognitive Services OpenAI User`` RBAC on the target resource.
3. ``AZURE_AI_FOUNDRY_ENDPOINT`` set in ``.env`` (or env var).
4. A model deployed (defaults to ``gpt-4o-mini``).

Run with::

    pytest tests/test_llm_live.py -m live -v

These tests are **excluded** from the default ``pytest`` run because they
require real Azure credentials and incur real API costs.
"""

from __future__ import annotations

import asyncio
import os

import pytest

# Skip entire module if no endpoint configured
_endpoint = os.getenv("AZURE_AI_FOUNDRY_ENDPOINT", "")
if not _endpoint:
    # Try loading from .env via pydantic-settings
    try:
        from src.config import get_settings as _gs

        _endpoint = _gs().llm.azure_endpoint or ""
    except Exception:
        _endpoint = ""

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not _endpoint, reason="No AZURE_AI_FOUNDRY_ENDPOINT configured"),
]


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_singletons() -> None:
    """Reset LLMClient and Settings singletons between tests."""
    import src.config as _cfg
    import src.llm.client as _mod

    _mod._llm_client = None
    _cfg._settings = None


# ── 1. LLMClient direct chat ───────────────────────────────────────────────


class TestLiveClientChat:
    """Prove LLMClient.chat() returns a real string from Azure OpenAI."""

    async def test_simple_prompt_returns_string(self) -> None:
        """Send a trivial prompt and confirm we get a non-empty string back."""
        from src.llm.client import LLMClient

        client = LLMClient()
        result = await client.chat(
            messages=[{"role": "user", "content": "Reply with exactly: HELLO"}],
            max_tokens=32,
            temperature=0.0,
        )
        assert result is not None, "LLMClient.chat() returned None — auth or endpoint issue"
        assert isinstance(result, str)
        assert len(result) > 0
        # The model should reply with something containing HELLO
        assert "HELLO" in result.upper(), f"Expected 'HELLO' in response, got: {result!r}"

    async def test_client_available_property(self) -> None:
        """Confirm .available is True after successful init."""
        from src.llm.client import LLMClient

        client = LLMClient()
        # Trigger init via chat
        await client.chat(
            messages=[{"role": "user", "content": "Say OK"}],
            max_tokens=8,
        )
        assert client.available is True, "Client should be available after successful call"

    async def test_multi_turn_conversation(self) -> None:
        """Verify multi-turn messages work correctly."""
        from src.llm.client import LLMClient

        client = LLMClient()
        messages = [
            {"role": "system", "content": "You are a calculator. Only reply with numbers."},
            {"role": "user", "content": "What is 2 + 2?"},
        ]
        result = await client.chat(messages=messages, max_tokens=16, temperature=0.0)
        assert result is not None
        assert "4" in result, f"Expected '4' in calculator response, got: {result!r}"

    async def test_singleton_returns_same_instance(self) -> None:
        """get_llm_client() returns the same singleton across calls."""
        from src.llm.client import get_llm_client

        c1 = get_llm_client()
        c2 = get_llm_client()
        assert c1 is c2


# ── 2. Agent _call_llm integration ─────────────────────────────────────────


class TestLiveAgentCallLLM:
    """Prove BaseAgent._call_llm() works end-to-end with real Azure."""

    async def test_plan_agent_live_llm(self) -> None:
        """PlanAgent gets a real LLM response for a planning request."""
        from src.agents.plan_agent import PlanAgent

        agent = PlanAgent()
        result = await agent._call_llm(
            [{"role": "user", "content": "Create a brief plan for analyzing server logs. Reply in under 50 words."}],
        )
        assert result is not None, "PlanAgent._call_llm() returned None"
        assert isinstance(result, str)
        assert len(result) > 10, f"Response suspiciously short: {result!r}"

    async def test_knowledge_base_agent_live_llm(self) -> None:
        """KnowledgeBaseAgent gets a real LLM response."""
        from src.agents.knowledge_base_agent import KnowledgeBaseAgent

        agent = KnowledgeBaseAgent()
        result = await agent._call_llm(
            [{"role": "user", "content": "Summarize what a knowledge base agent does in one sentence."}],
        )
        assert result is not None, "KnowledgeBaseAgent._call_llm() returned None"
        assert len(result) > 10

    async def test_generic_agent_live_llm(self) -> None:
        """GenericAgent gets a real LLM response."""
        from src.agents.generic import GenericAgent

        agent = GenericAgent(
            agent_id="test-live",
            description="Live test agent",
            system_prompt="You are a helpful test agent.",
        )
        result = await agent._call_llm(
            [{"role": "user", "content": "Reply with exactly: LIVE_TEST_OK"}],
        )
        assert result is not None, "GenericAgent._call_llm() returned None"
        assert "LIVE" in result.upper() or "OK" in result.upper()


# ── 3. DefaultAzureCredential verification ──────────────────────────────────


class TestLiveDefaultAzureCredential:
    """Confirm DefaultAzureCredential token flow works end-to-end."""

    async def test_credential_obtains_token(self) -> None:
        """DefaultAzureCredential can get a bearer token for cognitive services."""
        from azure.identity import DefaultAzureCredential

        credential = DefaultAzureCredential()
        token = credential.get_token("https://cognitiveservices.azure.com/.default")
        assert token is not None
        assert token.token, "Token string is empty"
        assert len(token.token) > 50, "Token suspiciously short"

    async def test_auth_method_is_azure_default(self) -> None:
        """Config should resolve to azure_default auth method."""
        from src.config import AuthMethod, get_settings

        settings = get_settings()
        assert settings.llm.auth_method == AuthMethod.AZURE_DEFAULT

    async def test_active_provider_is_azure(self) -> None:
        """With AZURE_AI_FOUNDRY_ENDPOINT set, provider should be Azure."""
        from src.config import LLMProvider, get_settings

        settings = get_settings()
        assert settings.llm.active_provider == LLMProvider.AZURE_AI_FOUNDRY


# ── 4. End-to-end pipeline smoke test ──────────────────────────────────────


class TestLivePipelineSmoke:
    """Lightweight pipeline test — just confirm the engine can use LLM routing."""

    async def test_engine_routes_with_llm(self) -> None:
        """OrchestratorEngine._route_with_llm returns a valid agent type."""
        from src.agents.knowledge_base_agent import KnowledgeBaseAgent
        from src.agents.log_analysis_agent import LogAnalysisAgent
        from src.agents.plan_agent import PlanAgent
        from src.orchestrator.engine import OrchestratorEngine

        engine = OrchestratorEngine()
        engine.register_agent("plan", PlanAgent())
        engine.register_agent("log_analysis", LogAnalysisAgent())
        engine.register_agent("knowledge_base", KnowledgeBaseAgent())
        result = await engine._route_with_llm("analyze the latest server error logs")
        # Should return a RoutingDecision or None
        if result is not None:
            assert hasattr(result, "primary_agent"), f"Unexpected type: {type(result)}"
            assert len(result.primary_agent) > 0


# ── 5. Error resilience ────────────────────────────────────────────────────


class TestLiveErrorResilience:
    """Verify graceful degradation even in live mode."""

    async def test_bad_model_returns_none(self) -> None:
        """Requesting a non-existent model should return None, not crash."""
        from src.llm.client import LLMClient

        client = LLMClient()
        result = await client.chat(
            messages=[{"role": "user", "content": "test"}],
            model="nonexistent-model-xyz-99",
            max_tokens=8,
        )
        # Should gracefully return None (the client catches API errors)
        assert result is None, f"Expected None for bad model, got: {result!r}"

    async def test_empty_messages_returns_none(self) -> None:
        """Empty message list should not crash."""
        from src.llm.client import LLMClient

        client = LLMClient()
        result = await client.chat(messages=[], max_tokens=8)
        # Behaviour varies — either None or an error response, but no crash
        # The important thing is no unhandled exception
        assert result is None or isinstance(result, str)
