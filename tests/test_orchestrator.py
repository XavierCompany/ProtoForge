"""Tests for the orchestrator engine."""

import pytest

from src.agents.plan_agent import PlanAgent
from src.agents.log_analysis_agent import LogAnalysisAgent
from src.agents.knowledge_base_agent import KnowledgeBaseAgent
from src.orchestrator.context import ConversationContext, MessageRole
from src.orchestrator.engine import OrchestratorEngine
from src.orchestrator.router import AgentType


@pytest.fixture
def engine() -> OrchestratorEngine:
    engine = OrchestratorEngine()
    engine.register_agent(AgentType.PLAN, PlanAgent())
    engine.register_agent(AgentType.LOG_ANALYSIS, LogAnalysisAgent())
    engine.register_agent(AgentType.KNOWLEDGE_BASE, KnowledgeBaseAgent())
    return engine


class TestOrchestratorEngine:
    @pytest.mark.asyncio
    async def test_process_routes_to_plan(self, engine: OrchestratorEngine) -> None:
        response = await engine.process("Create a plan to refactor the auth module")
        assert "Plan Agent" in response
        assert len(engine.context.messages) >= 2  # user + agent

    @pytest.mark.asyncio
    async def test_process_routes_to_log_analysis(self, engine: OrchestratorEngine) -> None:
        response = await engine.process("Analyze the error logs showing 500 errors")
        assert "Log Analysis" in response

    @pytest.mark.asyncio
    async def test_process_missing_agent(self, engine: OrchestratorEngine) -> None:
        response = await engine.process("Scan for security vulnerabilities")
        assert "No agent registered" in response or "security" in response.lower()

    @pytest.mark.asyncio
    async def test_context_accumulates(self, engine: OrchestratorEngine) -> None:
        await engine.process("Create a plan for the API")
        await engine.process("Now analyze the logs")
        assert len(engine.context.messages) >= 4

    def test_reset_context(self, engine: OrchestratorEngine) -> None:
        engine.context.add_user_message("test")
        old_session = engine.context.session_id
        engine.reset_context()
        assert engine.context.session_id != old_session
        assert len(engine.context.messages) == 0

    def test_get_status(self, engine: OrchestratorEngine) -> None:
        status = engine.get_status()
        assert "session_id" in status
        assert "registered_agents" in status
        assert len(status["registered_agents"]) == 3


class TestConversationContext:
    def test_add_user_message(self) -> None:
        ctx = ConversationContext()
        ctx.add_user_message("hello")
        assert len(ctx.messages) == 1
        assert ctx.messages[0].role == MessageRole.USER

    def test_add_agent_message(self) -> None:
        ctx = ConversationContext()
        ctx.add_agent_message("plan_agent", "here's the plan")
        assert ctx.messages[0].agent_id == "plan_agent"

    def test_working_memory(self) -> None:
        ctx = ConversationContext()
        ctx.set_memory("key", "value")
        assert ctx.get_memory("key") == "value"
        assert ctx.get_memory("missing", "default") == "default"

    def test_history_for_agent(self) -> None:
        ctx = ConversationContext()
        for i in range(25):
            ctx.add_user_message(f"message {i}")
        history = ctx.get_history_for_agent(last_n=10)
        assert len(history) == 10
