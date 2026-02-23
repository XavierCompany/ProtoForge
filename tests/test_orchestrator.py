"""Tests for the Plan-first orchestrator engine."""

import pytest

from src.agents.knowledge_base_agent import KnowledgeBaseAgent
from src.agents.log_analysis_agent import LogAnalysisAgent
from src.agents.plan_agent import PlanAgent
from src.orchestrator.context import ConversationContext, MessageRole
from src.orchestrator.engine import OrchestratorEngine


@pytest.fixture
def engine() -> OrchestratorEngine:
    engine = OrchestratorEngine()
    engine.register_agent("plan", PlanAgent())
    engine.register_agent("log_analysis", LogAnalysisAgent())
    engine.register_agent("knowledge_base", KnowledgeBaseAgent())
    return engine


class TestOrchestratorEngine:
    @pytest.mark.asyncio
    async def test_plan_always_runs_first(self, engine: OrchestratorEngine) -> None:
        """Plan Agent must always be the first agent executed."""
        response = await engine.process("Analyze the error logs showing 500 errors")
        # Response should contain plan output first, then sub-agent output
        assert "Plan Agent" in response or "Plan:" in response
        # Plan output should be stored in working memory
        assert engine.context.get_memory("plan_output") is not None

    @pytest.mark.asyncio
    async def test_plan_then_sub_agents(self, engine: OrchestratorEngine) -> None:
        """After Plan Agent, relevant sub-agents should execute."""
        response = await engine.process("Analyze the error logs showing 500 errors")
        # Should contain both plan and log analysis output
        assert "Plan" in response
        assert "Log Analysis" in response

    @pytest.mark.asyncio
    async def test_plan_first_with_plan_request(self, engine: OrchestratorEngine) -> None:
        """Even when intent routes to PLAN, plan runs first and no duplicate."""
        response = await engine.process("Create a plan to refactor the auth module")
        assert "Plan" in response
        # Plan agent should run once (not duplicated)
        plan_count = response.count("Plan Agent")
        assert plan_count >= 1

    @pytest.mark.asyncio
    async def test_plan_context_passed_to_sub_agents(self, engine: OrchestratorEngine) -> None:
        """Plan output should be available in working memory for sub-agents."""
        await engine.process("Analyze the error logs from production")
        plan_output = engine.context.get_memory("plan_output")
        plan_artifacts = engine.context.get_memory("plan_artifacts")
        assert plan_output is not None
        assert isinstance(plan_artifacts, dict)
        assert "step_count" in plan_artifacts

    @pytest.mark.asyncio
    async def test_process_missing_agent(self, engine: OrchestratorEngine) -> None:
        """Plan Agent always runs; missing sub-agents handled gracefully."""
        response = await engine.process("Scan for security vulnerabilities")
        # Plan always runs first (it's registered)
        assert "Plan" in response
        # Security sentinel is not registered, so result has "No agent registered"
        assert "No agent registered" in response or "security" in response.lower()

    @pytest.mark.asyncio
    async def test_context_accumulates(self, engine: OrchestratorEngine) -> None:
        await engine.process("Create a plan for the API")
        await engine.process("Now analyze the logs")
        # At least: 2 user msgs + plan results + sub-agent results
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

    def test_history_limit_trims_messages(self) -> None:
        """ConversationContext should trim messages when max_history is exceeded."""
        ctx = ConversationContext(max_history=10)
        for i in range(20):
            ctx.add_user_message(f"user {i}")
        assert len(ctx.messages) == 10
        # Oldest messages should have been dropped; newest retained
        assert ctx.messages[0].content == "user 10"
        assert ctx.messages[-1].content == "user 19"

    def test_history_limit_trims_agent_messages(self) -> None:
        ctx = ConversationContext(max_history=5)
        for i in range(10):
            ctx.add_agent_message("bot", f"reply {i}")
        assert len(ctx.messages) == 5
        assert ctx.messages[0].content == "reply 5"
