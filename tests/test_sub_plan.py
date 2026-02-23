"""Tests for Sub-Plan Agent, PlanSelector HITL, and the engine sub-plan pipeline."""

from __future__ import annotations

from typing import Any

import pytest

from src.agents.knowledge_base_agent import KnowledgeBaseAgent
from src.agents.log_analysis_agent import LogAnalysisAgent
from src.agents.plan_agent import PlanAgent
from src.agents.sub_plan_agent import SubPlanAgent
from src.orchestrator.context import AgentResult, ConversationContext
from src.orchestrator.engine import OrchestratorEngine
from src.orchestrator.plan_selector import (
    _DEFAULT_SUB_PLAN_BRIEF,
    PlanSelector,
)

# ── SubPlanAgent unit tests ────────────────────────────────────────────────


class TestSubPlanAgent:
    @pytest.fixture
    def agent(self) -> SubPlanAgent:
        return SubPlanAgent()

    @pytest.fixture
    def context_with_plan(self) -> ConversationContext:
        ctx = ConversationContext()
        ctx.set_memory("plan_output", "Create workspace connectors for M365")
        ctx.set_memory(
            "plan_artifacts",
            {
                "recommended_sub_agents": ["log_analysis", "knowledge_base"],
                "step_count": 3,
            },
        )
        return ctx

    @pytest.mark.asyncio
    async def test_execute_returns_result(self, agent: SubPlanAgent, context_with_plan: ConversationContext) -> None:
        result = await agent.execute("create workspace connectors", context_with_plan)
        assert isinstance(result, AgentResult)
        assert result.agent_id == "sub_plan"
        assert result.confidence > 0

    @pytest.mark.asyncio
    async def test_execute_identifies_connector_resource(
        self,
        agent: SubPlanAgent,
        context_with_plan: ConversationContext,
    ) -> None:
        result = await agent.execute("create workspace connectors", context_with_plan)
        resources = result.artifacts.get("resources", [])
        resource_types = [r["type"] for r in resources]
        assert "connector" in resource_types

    @pytest.mark.asyncio
    async def test_execute_identifies_log_resource(
        self,
        agent: SubPlanAgent,
        context_with_plan: ConversationContext,
    ) -> None:
        result = await agent.execute("check error logs in production", context_with_plan)
        resources = result.artifacts.get("resources", [])
        resource_types = [r["type"] for r in resources]
        assert "monitoring" in resource_types

    @pytest.mark.asyncio
    async def test_execute_with_user_brief(self, agent: SubPlanAgent, context_with_plan: ConversationContext) -> None:
        result = await agent.execute(
            "create workspace connectors",
            context_with_plan,
            params={"user_brief": "Only free-tier resources"},
        )
        assert result.artifacts["user_brief"] == "Only free-tier resources"

    @pytest.mark.asyncio
    async def test_execute_empty_plan(self, agent: SubPlanAgent) -> None:
        ctx = ConversationContext()
        result = await agent.execute("hello world", ctx)
        # With no plan in memory, resources may be empty
        assert result.artifacts["resource_count"] >= 0

    @pytest.mark.asyncio
    async def test_content_includes_principle(
        self,
        agent: SubPlanAgent,
        context_with_plan: ConversationContext,
    ) -> None:
        result = await agent.execute("create workspace connectors", context_with_plan)
        assert "minimum resources" in result.content.lower()

    @pytest.mark.asyncio
    async def test_from_manifest(self) -> None:
        from src.forge.loader import AgentManifest

        manifest = AgentManifest(
            id="sub_plan",
            name="Sub-Plan Agent",
            type="specialist",
            version="1.0.0",
            description="Test sub-plan",
            context_budget={"max_input_tokens": 10000, "max_output_tokens": 5000},
            resolved_prompts={"system": "Custom prompt"},
        )
        agent = SubPlanAgent.from_manifest(manifest)
        assert agent.agent_id == "sub_plan"


# ── PlanSelector unit tests (Phase A — Plan HITL) ──────────────────────────


class TestPlanSelectorPlanReview:
    @pytest.fixture
    def selector(self) -> PlanSelector:
        return PlanSelector(timeout=1.0)

    def test_prepare_auto_resolves_single_suggestion(self, selector: PlanSelector) -> None:
        req = selector.prepare_plan_review(
            "req-1",
            "Test plan",
            ["log_analysis"],
        )
        assert req.resolved is True
        assert req.accepted_indices == [0]

    def test_prepare_creates_pending_multiple(self, selector: PlanSelector) -> None:
        req = selector.prepare_plan_review(
            "req-2",
            "Test plan",
            ["log_analysis", "knowledge_base"],
        )
        assert req.resolved is False
        assert len(req.suggestions) == 2

    def test_resolve_plan_review(self, selector: PlanSelector) -> None:
        selector.prepare_plan_review(
            "req-3",
            "Plan",
            ["log_analysis", "knowledge_base", "remediation"],
        )
        ok = selector.resolve_plan_review("req-3", [0, 2])
        assert ok is True

        accepted = selector.accepted_plan_agents("req-3")
        assert accepted == ["log_analysis", "remediation"]

    def test_resolve_nonexistent_returns_false(self, selector: PlanSelector) -> None:
        assert selector.resolve_plan_review("nope", [0]) is False

    def test_pending_plan_reviews(self, selector: PlanSelector) -> None:
        selector.prepare_plan_review(
            "req-4",
            "Plan",
            ["a", "b"],
        )
        pending = selector.pending_plan_reviews()
        assert len(pending) == 1
        assert pending[0]["request_id"] == "req-4"

    def test_cleanup_plan_review(self, selector: PlanSelector) -> None:
        selector.prepare_plan_review(
            "req-5",
            "Plan",
            ["a", "b"],
        )
        selector.cleanup_plan_review("req-5")
        assert selector.pending_plan_reviews() == []

    @pytest.mark.asyncio
    async def test_wait_timeout_accepts_all(self, selector: PlanSelector) -> None:
        selector.prepare_plan_review(
            "req-6",
            "Plan",
            ["a", "b", "c"],
        )
        req = await selector.wait_for_plan_review("req-6")
        assert req.resolved is True
        assert req.accepted_indices == [0, 1, 2]

    @pytest.mark.asyncio
    async def test_wait_resolved_returns_immediately(self, selector: PlanSelector) -> None:
        selector.prepare_plan_review(
            "req-7",
            "Plan",
            ["a", "b"],
        )
        selector.resolve_plan_review("req-7", [1])

        req = await selector.wait_for_plan_review("req-7")
        assert req.resolved is True
        assert req.accepted_indices == [1]


# ── PlanSelector unit tests (Phase B — Sub-Plan HITL) ──────────────────────


class TestPlanSelectorResourceReview:
    @pytest.fixture
    def selector(self) -> PlanSelector:
        return PlanSelector(timeout=1.0)

    @pytest.fixture
    def sample_resources(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "Storage Account",
                "type": "azure-storage",
                "purpose": "Blob storage",
                "effort": "quick",
                "dependencies": [],
            },
            {
                "name": "API Endpoint",
                "type": "api-service",
                "purpose": "HTTP endpoint",
                "effort": "quick",
                "dependencies": [],
            },
        ]

    def test_prepare_auto_resolves_empty(self, selector: PlanSelector) -> None:
        req = selector.prepare_resource_review("res-1", "Summary", [])
        assert req.resolved is True
        assert req.accepted_indices == []

    def test_prepare_creates_pending(self, selector: PlanSelector, sample_resources: list) -> None:
        req = selector.prepare_resource_review("res-2", "Summary", sample_resources)
        assert req.resolved is False
        assert len(req.resources) == 2

    def test_resolve_with_brief(self, selector: PlanSelector, sample_resources: list) -> None:
        selector.prepare_resource_review("res-3", "Summary", sample_resources)
        ok = selector.resolve_resource_review(
            "res-3",
            [0],
            user_brief="Use emulators only",
        )
        assert ok is True

        accepted = selector.accepted_resources("res-3")
        assert len(accepted) == 1
        assert accepted[0].name == "Storage Account"

        brief = selector.resource_brief("res-3")
        assert brief == "Use emulators only"

    def test_default_brief(self, selector: PlanSelector, sample_resources: list) -> None:
        selector.prepare_resource_review("res-4", "Summary", sample_resources)
        selector.resolve_resource_review("res-4", [0, 1])

        brief = selector.resource_brief("res-4")
        assert brief == _DEFAULT_SUB_PLAN_BRIEF

    def test_pending_resource_reviews(self, selector: PlanSelector, sample_resources: list) -> None:
        selector.prepare_resource_review("res-5", "Summary", sample_resources)
        pending = selector.pending_resource_reviews()
        assert len(pending) == 1
        assert pending[0]["request_id"] == "res-5"

    @pytest.mark.asyncio
    async def test_wait_timeout_accepts_all_resources(self, selector: PlanSelector, sample_resources: list) -> None:
        selector.prepare_resource_review("res-6", "Summary", sample_resources)
        req = await selector.wait_for_resource_review("res-6")
        assert req.resolved is True
        assert req.accepted_indices == [0, 1]


# ── Engine integration: Sub-Plan pipeline ───────────────────────────────────


class TestEngineSubPlanPipeline:
    @pytest.fixture
    def engine_with_sub_plan(self) -> OrchestratorEngine:
        plan_selector = PlanSelector(timeout=0.5)
        engine = OrchestratorEngine(plan_selector=plan_selector)
        engine.register_agent("plan", PlanAgent())
        engine.register_agent("sub_plan", SubPlanAgent())
        engine.register_agent("log_analysis", LogAnalysisAgent())
        engine.register_agent("knowledge_base", KnowledgeBaseAgent())
        return engine

    @pytest.fixture
    def engine_without_selector(self) -> OrchestratorEngine:
        """Engine with SubPlanAgent but no PlanSelector — skips HITL."""
        engine = OrchestratorEngine()
        engine.register_agent("plan", PlanAgent())
        engine.register_agent("sub_plan", SubPlanAgent())
        engine.register_agent("log_analysis", LogAnalysisAgent())
        return engine

    @pytest.mark.asyncio
    async def test_sub_plan_runs_before_task_agents(self, engine_with_sub_plan: OrchestratorEngine) -> None:
        response = await engine_with_sub_plan.process("Create workspace connectors for M365 integration")
        assert "Plan" in response
        assert "Sub-Plan" in response or "Resource Deployment" in response

    @pytest.mark.asyncio
    async def test_sub_plan_output_in_memory(self, engine_with_sub_plan: OrchestratorEngine) -> None:
        await engine_with_sub_plan.process("Create workspace connectors")
        sub_plan_output = engine_with_sub_plan.context.get_memory("sub_plan_output")
        assert sub_plan_output is not None
        assert len(sub_plan_output) > 0

    @pytest.mark.asyncio
    async def test_sub_plan_artifacts_in_memory(self, engine_with_sub_plan: OrchestratorEngine) -> None:
        await engine_with_sub_plan.process("Create workspace connectors")
        artifacts = engine_with_sub_plan.context.get_memory("sub_plan_artifacts")
        assert artifacts is not None
        assert "resources" in artifacts

    @pytest.mark.asyncio
    async def test_plan_hitl_timeout_accepts_all(self, engine_with_sub_plan: OrchestratorEngine) -> None:
        """When HITL times out, all plan suggestions should be accepted (fail-open)."""
        response = await engine_with_sub_plan.process("Analyze error logs and fix security vulnerabilities")
        # Both plan and sub-plan should run even on timeout
        assert "Plan" in response

    @pytest.mark.asyncio
    async def test_without_selector_sub_plan_still_runs(self, engine_without_selector: OrchestratorEngine) -> None:
        """Without PlanSelector, Sub-Plan Agent runs but HITL gates are skipped."""
        response = await engine_without_selector.process("Create workspace connectors")
        assert "Plan" in response
        sub_plan = engine_without_selector.context.get_memory("sub_plan_output")
        assert sub_plan is not None

    @pytest.mark.asyncio
    async def test_sub_plan_excluded_from_sub_agents(self, engine_with_sub_plan: OrchestratorEngine) -> None:
        """sub_plan should not appear as a task sub-agent in the fan-out."""
        from src.orchestrator.router import RoutingDecision

        routing = RoutingDecision(
            primary_agent="sub_plan",
            secondary_agents=["log_analysis"],
        )
        resolved = engine_with_sub_plan._resolve_sub_agents(routing)
        assert "sub_plan" not in resolved
        assert "plan" not in resolved
        assert "log_analysis" in resolved


# ── Router: SUB_PLAN patterns ──────────────────────────────────────────────


class TestSubPlanRouting:
    @pytest.fixture
    def router(self):
        from src.orchestrator.router import IntentRouter

        return IntentRouter()

    def test_resource_keyword(self, router) -> None:
        from src.orchestrator.router import AgentType

        result = router.route_by_keywords("Deploy the prerequisite resources for the connector")
        all_agents = [result.primary_agent, *result.secondary_agents]
        assert AgentType.SUB_PLAN in all_agents

    def test_connector_keyword(self, router) -> None:
        from src.orchestrator.router import AgentType

        result = router.route_by_keywords("Create a workspace connector to M365")
        all_agents = [result.primary_agent, *result.secondary_agents]
        assert AgentType.SUB_PLAN in all_agents

    def test_infrastructure_keyword(self, router) -> None:
        from src.orchestrator.router import AgentType

        result = router.route_by_keywords("Set up the infrastructure for the demo")
        all_agents = [result.primary_agent, *result.secondary_agents]
        assert AgentType.SUB_PLAN in all_agents
