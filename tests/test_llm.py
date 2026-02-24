"""Tests for the LLM client and agent LLM integration.

Every test mocks the LLM backend -- no real Azure / OpenAI calls are made.
The LLMClient singleton is reset between tests via the ``reset_llm_singleton``
fixture to avoid cross-test contamination.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import AuthMethod, LLMProvider
from src.llm.client import LLMClient, get_llm_client
from src.orchestrator.context import ConversationContext

# -- Fixtures ----------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_llm_singleton() -> None:
    """Reset the module-level LLMClient singleton between tests."""
    import src.llm.client as _mod

    _mod._llm_client = None


@pytest.fixture
def fresh_client() -> LLMClient:
    """Return a brand-new LLMClient (not forced-initialised)."""
    return LLMClient()


@pytest.fixture
def mock_settings_no_endpoint() -> MagicMock:
    """Settings where no Azure endpoint is configured."""
    settings = MagicMock()
    settings.llm.azure_endpoint = ""
    settings.llm.openai_api_key = ""
    settings.llm.active_provider = LLMProvider.AZURE_AI_FOUNDRY
    settings.llm.auth_method = AuthMethod.AZURE_DEFAULT
    settings.llm.azure_model = "gpt-5.3-codex"
    settings.llm.azure_api_version = "2026-01-01"
    settings.llm.openai_model = "gpt-4o"
    return settings


@pytest.fixture
def mock_settings_azure(mock_settings_no_endpoint: MagicMock) -> MagicMock:
    """Settings with a valid Azure endpoint + DefaultAzureCredential."""
    mock_settings_no_endpoint.llm.azure_endpoint = "https://test.openai.azure.com"
    return mock_settings_no_endpoint


@pytest.fixture
def mock_settings_azure_apikey(mock_settings_azure: MagicMock) -> MagicMock:
    """Settings with Azure endpoint + API key auth."""
    mock_settings_azure.llm.auth_method = AuthMethod.API_KEY
    mock_settings_azure.llm.openai_api_key = "sk-test-key"
    return mock_settings_azure


@pytest.fixture
def mock_settings_openai(mock_settings_no_endpoint: MagicMock) -> MagicMock:
    """Settings with direct OpenAI provider + key."""
    mock_settings_no_endpoint.llm.active_provider = LLMProvider.OPENAI
    mock_settings_no_endpoint.llm.openai_api_key = "sk-openai-key"
    return mock_settings_no_endpoint


@pytest.fixture
def conversation_context() -> ConversationContext:
    """Minimal ConversationContext for agent tests."""
    return ConversationContext()


# -- Helpers -----------------------------------------------------------------


def _make_chat_response(text: str) -> MagicMock:
    """Build a mock chat completion response object."""
    choice = MagicMock()
    choice.message.content = text
    resp = MagicMock()
    resp.choices = [choice]
    return resp


# ============================================================================
#  1. LLMClient -- graceful degradation
# ============================================================================


class TestLLMClientDegradation:
    """When no endpoint/key is configured, chat() returns None."""

    @pytest.mark.asyncio
    async def test_chat_returns_none_no_config(
        self,
        fresh_client: LLMClient,
        mock_settings_no_endpoint: MagicMock,
    ) -> None:
        with patch(
            "src.llm.client.get_settings",
            return_value=mock_settings_no_endpoint,
        ):
            result = await fresh_client.chat([{"role": "user", "content": "hi"}])
        assert result is None

    @pytest.mark.asyncio
    async def test_available_false_no_config(
        self,
        fresh_client: LLMClient,
        mock_settings_no_endpoint: MagicMock,
    ) -> None:
        with patch(
            "src.llm.client.get_settings",
            return_value=mock_settings_no_endpoint,
        ):
            assert fresh_client.available is False

    @pytest.mark.asyncio
    async def test_chat_returns_none_on_sdk_error(
        self,
        fresh_client: LLMClient,
        mock_settings_azure: MagicMock,
    ) -> None:
        """Even with a configured client, SDK errors return None."""
        mock_sdk = AsyncMock()
        mock_sdk.chat.completions.create = AsyncMock(side_effect=RuntimeError("network error"))

        with (
            patch(
                "src.llm.client.get_settings",
                return_value=mock_settings_azure,
            ),
            patch("openai.AsyncAzureOpenAI", return_value=mock_sdk),
            patch("azure.identity.DefaultAzureCredential"),
            patch(
                "azure.identity.get_bearer_token_provider",
                return_value=lambda: "tok",
            ),
        ):
            result = await fresh_client.chat([{"role": "user", "content": "hi"}])
        assert result is None

    @pytest.mark.asyncio
    async def test_chat_returns_none_on_empty_content(
        self,
        fresh_client: LLMClient,
        mock_settings_azure: MagicMock,
    ) -> None:
        """When API returns empty content string, chat() returns None."""
        mock_sdk = AsyncMock()
        mock_sdk.chat.completions.create = AsyncMock(return_value=_make_chat_response(""))

        with (
            patch(
                "src.llm.client.get_settings",
                return_value=mock_settings_azure,
            ),
            patch("openai.AsyncAzureOpenAI", return_value=mock_sdk),
            patch("azure.identity.DefaultAzureCredential"),
            patch(
                "azure.identity.get_bearer_token_provider",
                return_value=lambda: "tok",
            ),
        ):
            result = await fresh_client.chat([{"role": "user", "content": "hi"}])
        # empty string -> `content or None` -> None
        assert result is None


# ============================================================================
#  2. Azure DefaultAzureCredential init
# ============================================================================


class TestAzureDefaultCredential:
    def test_init_creates_client(
        self,
        fresh_client: LLMClient,
        mock_settings_azure: MagicMock,
    ) -> None:
        mock_azure_cls = MagicMock()
        with (
            patch(
                "src.llm.client.get_settings",
                return_value=mock_settings_azure,
            ),
            patch("openai.AsyncAzureOpenAI", mock_azure_cls),
            patch("azure.identity.DefaultAzureCredential"),
            patch(
                "azure.identity.get_bearer_token_provider",
                return_value=lambda: "tok",
            ),
        ):
            # Trigger init via `available` property
            assert fresh_client.available is True
        mock_azure_cls.assert_called_once()

    @pytest.mark.asyncio
    async def test_chat_returns_text(
        self,
        fresh_client: LLMClient,
        mock_settings_azure: MagicMock,
    ) -> None:
        mock_sdk = AsyncMock()
        mock_sdk.chat.completions.create = AsyncMock(return_value=_make_chat_response("Hello from Azure!"))

        with (
            patch(
                "src.llm.client.get_settings",
                return_value=mock_settings_azure,
            ),
            patch("openai.AsyncAzureOpenAI", return_value=mock_sdk),
            patch("azure.identity.DefaultAzureCredential"),
            patch(
                "azure.identity.get_bearer_token_provider",
                return_value=lambda: "tok",
            ),
        ):
            result = await fresh_client.chat([{"role": "user", "content": "hi"}])
        assert result == "Hello from Azure!"


# ============================================================================
#  3. Azure API-key auth
# ============================================================================


class TestAzureAPIKey:
    def test_no_key_means_unavailable(
        self,
        fresh_client: LLMClient,
        mock_settings_azure: MagicMock,
    ) -> None:
        mock_settings_azure.llm.auth_method = AuthMethod.API_KEY
        mock_settings_azure.llm.openai_api_key = ""
        with patch(
            "src.llm.client.get_settings",
            return_value=mock_settings_azure,
        ):
            assert fresh_client.available is False

    def test_with_key_inits_client(
        self,
        fresh_client: LLMClient,
        mock_settings_azure_apikey: MagicMock,
    ) -> None:
        mock_azure_cls = MagicMock()
        with (
            patch(
                "src.llm.client.get_settings",
                return_value=mock_settings_azure_apikey,
            ),
            patch("openai.AsyncAzureOpenAI", mock_azure_cls),
        ):
            assert fresh_client.available is True
        mock_azure_cls.assert_called_once()
        call_kwargs = mock_azure_cls.call_args.kwargs
        assert call_kwargs["api_key"] == "sk-test-key"


# ============================================================================
#  4. Direct OpenAI
# ============================================================================


class TestOpenAIDirect:
    def test_no_key_means_unavailable(
        self,
        fresh_client: LLMClient,
        mock_settings_openai: MagicMock,
    ) -> None:
        mock_settings_openai.llm.openai_api_key = ""
        with patch(
            "src.llm.client.get_settings",
            return_value=mock_settings_openai,
        ):
            assert fresh_client.available is False

    def test_with_key_inits_client(
        self,
        fresh_client: LLMClient,
        mock_settings_openai: MagicMock,
    ) -> None:
        mock_openai_cls = MagicMock()
        with (
            patch(
                "src.llm.client.get_settings",
                return_value=mock_settings_openai,
            ),
            patch("openai.AsyncOpenAI", mock_openai_cls),
        ):
            assert fresh_client.available is True
        mock_openai_cls.assert_called_once()


# ============================================================================
#  5. Singleton
# ============================================================================


class TestSingleton:
    def test_same_instance(self) -> None:
        a = get_llm_client()
        b = get_llm_client()
        assert a is b

    def test_reset_creates_new(self) -> None:
        a = get_llm_client()
        import src.llm.client as _mod

        _mod._llm_client = None
        b = get_llm_client()
        assert a is not b


# ============================================================================
#  6. Model resolution
# ============================================================================


class TestModelResolution:
    def test_azure_model(self, mock_settings_azure: MagicMock) -> None:
        assert LLMClient._resolve_model(mock_settings_azure) == "gpt-5.3-codex"

    def test_openai_model(self, mock_settings_openai: MagicMock) -> None:
        assert LLMClient._resolve_model(mock_settings_openai) == "gpt-4o"


# ============================================================================
#  7. BaseAgent._call_llm delegation
# ============================================================================


class TestAgentCallLLM:
    @pytest.mark.asyncio
    async def test_call_llm_delegates_to_client(self) -> None:
        """_call_llm passes messages through to get_llm_client().chat()."""
        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(return_value="LLM says hi")

        with patch("src.llm.client.get_llm_client", return_value=mock_client):
            from src.agents.base import BaseAgent

            # Create a minimal concrete subclass
            class _TestAgent(BaseAgent):
                async def execute(
                    self,
                    message: str,
                    context: Any,  # noqa: ARG002
                    params: Any = None,  # noqa: ARG002
                ) -> Any:
                    return await self._call_llm([{"role": "user", "content": message}])

            agent = _TestAgent.__new__(_TestAgent)
            agent._agent_id = "test"
            agent._description = "test"
            agent._system_prompt = "test"
            agent._manifest = {}

            result = await agent.execute("hello", None)
        assert result == "LLM says hi"


# ============================================================================
#  8. GenericAgent -- LLM path + fallback
# ============================================================================


class TestGenericAgentLLM:
    @pytest.mark.asyncio
    async def test_llm_path(self, conversation_context: ConversationContext) -> None:
        from src.agents.generic import GenericAgent

        agent = GenericAgent(
            agent_id="code_research",
            description="Research agent",
            system_prompt="You research code",
        )
        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(return_value="Deep code analysis result")

        with patch("src.llm.client.get_llm_client", return_value=mock_client):
            result = await agent.execute("explain asyncio", conversation_context)

        assert result.content == "Deep code analysis result"
        assert result.confidence == 0.85
        assert result.artifacts["source"] == "llm"

    @pytest.mark.asyncio
    async def test_fallback_path(self, conversation_context: ConversationContext) -> None:
        from src.agents.generic import GenericAgent

        agent = GenericAgent(
            agent_id="code_research",
            description="Research agent",
            system_prompt="You research code",
        )
        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(return_value=None)

        with patch("src.llm.client.get_llm_client", return_value=mock_client):
            result = await agent.execute("explain asyncio", conversation_context)

        assert "Research agent" in result.content
        assert result.confidence == 0.6
        assert result.artifacts["source"] == "generic_agent"


# ============================================================================
#  9. PlanAgent -- LLM path + fallback
# ============================================================================


class TestPlanAgentLLM:
    @pytest.mark.asyncio
    async def test_llm_path(self, conversation_context: ConversationContext) -> None:
        from src.agents.plan_agent import PlanAgent

        agent = PlanAgent(
            agent_id="plan",
            description="Plan Agent",
            system_prompt="You are a top-level coordinator",
        )
        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(return_value="1. Step one\n2. Step two")

        with patch("src.llm.client.get_llm_client", return_value=mock_client):
            result = await agent.execute("deploy to staging", conversation_context)

        assert result.content == "1. Step one\n2. Step two"
        assert result.confidence == 0.90
        assert result.artifacts["source"] == "llm"
        assert "recommended_sub_agents" in result.artifacts

    @pytest.mark.asyncio
    async def test_fallback_path(self, conversation_context: ConversationContext) -> None:
        from src.agents.plan_agent import PlanAgent

        agent = PlanAgent(
            agent_id="plan",
            description="Plan Agent",
            system_prompt="You are a top-level coordinator",
        )
        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(return_value=None)

        with patch("src.llm.client.get_llm_client", return_value=mock_client):
            result = await agent.execute("deploy to staging", conversation_context)

        assert "Plan Agent" in result.content
        assert "Coordination Plan" in result.content
        # fallback has no "source" key in artifacts
        assert "source" not in result.artifacts


# ============================================================================
# 10. Specialist agents -- LLM integration
# ============================================================================


class TestSpecialistAgentLLM:
    """Verify specialist agents enrich prompts and use LLM correctly."""

    @pytest.mark.asyncio
    async def test_log_analysis_patterns_enrichment(self, conversation_context: ConversationContext) -> None:
        from src.agents.log_analysis_agent import LogAnalysisAgent

        agent = LogAnalysisAgent(
            agent_id="log_analysis",
            description="Log analysis",
            system_prompt="Analyse logs",
        )
        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(return_value="Root cause: timeout in DB pool")

        with patch("src.llm.client.get_llm_client", return_value=mock_client):
            # Message contains "timeout" -> pattern detection enriches prompt
            result = await agent.execute(
                "2024-01-15T10:30:00 ERROR timeout connecting to DB",
                conversation_context,
            )

        assert result.content == "Root cause: timeout in DB pool"
        assert result.confidence == 0.9  # patterns found -> 0.9
        assert result.artifacts["source"] == "llm"
        assert len(result.artifacts["patterns_found"]) > 0

    @pytest.mark.asyncio
    async def test_log_analysis_fallback_with_patterns(self, conversation_context: ConversationContext) -> None:
        from src.agents.log_analysis_agent import LogAnalysisAgent

        agent = LogAnalysisAgent(
            agent_id="log_analysis",
            description="Log analysis",
            system_prompt="Analyse logs",
        )
        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(return_value=None)

        with patch("src.llm.client.get_llm_client", return_value=mock_client):
            result = await agent.execute(
                "ERROR: 500 Internal Server Error",
                conversation_context,
            )

        assert "Log Analysis Report" in result.content
        assert result.confidence == 0.7  # patterns found, no LLM

    @pytest.mark.asyncio
    async def test_security_sentinel_llm(self, conversation_context: ConversationContext) -> None:
        from src.agents.security_sentinel_agent import SecuritySentinelAgent

        agent = SecuritySentinelAgent(
            agent_id="security_sentinel",
            description="Security",
            system_prompt="Analyse security",
        )
        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(return_value="SQL injection risk in query builder")

        with patch("src.llm.client.get_llm_client", return_value=mock_client):
            result = await agent.execute(
                "Review this SQL query construction for injection risks",
                conversation_context,
            )

        assert result.content == "SQL injection risk in query builder"
        assert result.artifacts["source"] == "llm"

    @pytest.mark.asyncio
    async def test_remediation_llm(self, conversation_context: ConversationContext) -> None:
        from src.agents.remediation_agent import RemediationAgent

        agent = RemediationAgent(
            agent_id="remediation",
            description="Remediation",
            system_prompt="Fix issues",
        )
        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(return_value="Apply connection pool increase")

        with patch("src.llm.client.get_llm_client", return_value=mock_client):
            result = await agent.execute(
                "Fix the database connection timeout",
                conversation_context,
            )

        assert result.content == "Apply connection pool increase"
        assert result.artifacts["source"] == "llm"

    @pytest.mark.asyncio
    async def test_knowledge_base_llm(self, conversation_context: ConversationContext) -> None:
        from src.agents.knowledge_base_agent import KnowledgeBaseAgent

        agent = KnowledgeBaseAgent(
            agent_id="knowledge_base",
            description="Knowledge Base",
            system_prompt="Answer questions",
        )
        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(return_value="ProtoForge uses a plan-first approach")

        with patch("src.llm.client.get_llm_client", return_value=mock_client):
            result = await agent.execute(
                "How does ProtoForge work?",
                conversation_context,
            )

        assert result.content == "ProtoForge uses a plan-first approach"
        assert result.confidence == 0.85
        assert result.artifacts["source"] == "llm"

    @pytest.mark.asyncio
    async def test_sub_plan_llm(self, conversation_context: ConversationContext) -> None:
        from src.agents.sub_plan_agent import SubPlanAgent

        agent = SubPlanAgent(
            agent_id="sub_plan",
            description="Sub-Plan Agent",
            system_prompt="Create sub-plans",
        )
        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(return_value="Sub-plan: 1. Scan logs 2. Remediate")

        with patch("src.llm.client.get_llm_client", return_value=mock_client):
            result = await agent.execute(
                "Create a sub-plan for log analysis",
                conversation_context,
            )

        assert result.content == "Sub-plan: 1. Scan logs 2. Remediate"
        assert result.confidence == 0.85
        assert result.artifacts["source"] == "llm"


# ============================================================================
# 11. OrchestratorEngine._route_with_llm
# ============================================================================


class TestEngineLLMRouting:
    """Test _route_with_llm on the OrchestratorEngine."""

    @pytest.mark.asyncio
    async def test_parses_json_response(self, base_engine: Any) -> None:
        llm_json = json.dumps(
            {
                "primary_agent": "log_analysis",
                "secondary_agents": ["remediation"],
                "confidence": 0.92,
                "reasoning": "Log error analysis needed",
                "extracted_params": {"severity": "high"},
            }
        )
        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(return_value=llm_json)

        with patch("src.llm.client.get_llm_client", return_value=mock_client):
            decision = await base_engine._route_with_llm("analyse these server logs")

        assert decision is not None
        assert decision.primary_agent == "log_analysis"
        assert "remediation" in decision.secondary_agents
        assert decision.confidence == 0.92

    @pytest.mark.asyncio
    async def test_bad_json_returns_none(self, base_engine: Any) -> None:
        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(return_value="not valid json {{{")

        with patch("src.llm.client.get_llm_client", return_value=mock_client):
            decision = await base_engine._route_with_llm("some message")

        assert decision is None

    @pytest.mark.asyncio
    async def test_llm_unavailable_returns_none(self, base_engine: Any) -> None:
        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(return_value=None)

        with patch("src.llm.client.get_llm_client", return_value=mock_client):
            decision = await base_engine._route_with_llm("some message")

        assert decision is None


# ============================================================================
# 12. Provider not-implemented path
# ============================================================================


class TestProviderNotImplemented:
    def test_anthropic_stays_unavailable(self, fresh_client: LLMClient) -> None:
        settings = MagicMock()
        settings.llm.active_provider = LLMProvider.ANTHROPIC
        with patch("src.llm.client.get_settings", return_value=settings):
            assert fresh_client.available is False

    def test_google_stays_unavailable(self, fresh_client: LLMClient) -> None:
        settings = MagicMock()
        settings.llm.active_provider = LLMProvider.GOOGLE
        with patch("src.llm.client.get_settings", return_value=settings):
            assert fresh_client.available is False
