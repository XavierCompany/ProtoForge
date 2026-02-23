"""Shared test fixtures and configuration for the ProtoForge test suite."""

from __future__ import annotations

import pytest

from src.agents.github_tracker_agent import GitHubTrackerAgent
from src.agents.knowledge_base_agent import KnowledgeBaseAgent
from src.agents.log_analysis_agent import LogAnalysisAgent
from src.agents.plan_agent import PlanAgent
from src.agents.sub_plan_agent import SubPlanAgent
from src.orchestrator.engine import OrchestratorEngine
from src.orchestrator.plan_selector import PlanSelector

# ── Reusable agent instances ──────────────────────────────────────────────


@pytest.fixture
def plan_agent() -> PlanAgent:
    return PlanAgent()


@pytest.fixture
def sub_plan_agent() -> SubPlanAgent:
    return SubPlanAgent()


@pytest.fixture
def github_tracker_agent() -> GitHubTrackerAgent:
    return GitHubTrackerAgent()


@pytest.fixture
def log_analysis_agent() -> LogAnalysisAgent:
    return LogAnalysisAgent()


@pytest.fixture
def knowledge_base_agent() -> KnowledgeBaseAgent:
    return KnowledgeBaseAgent()


# ── Pre-wired engines ────────────────────────────────────────────────────


@pytest.fixture
def base_engine() -> OrchestratorEngine:
    """Engine with Plan + Log Analysis + Knowledge Base (no HITL)."""
    engine = OrchestratorEngine()
    engine.register_agent("plan", PlanAgent())
    engine.register_agent("log_analysis", LogAnalysisAgent())
    engine.register_agent("knowledge_base", KnowledgeBaseAgent())
    return engine


@pytest.fixture
def engine_with_sub_plan() -> OrchestratorEngine:
    """Engine with Plan + Sub-Plan + Log Analysis + Knowledge Base (HITL)."""
    plan_selector = PlanSelector(timeout=0.5)
    engine = OrchestratorEngine(plan_selector=plan_selector)
    engine.register_agent("plan", PlanAgent())
    engine.register_agent("sub_plan", SubPlanAgent())
    engine.register_agent("log_analysis", LogAnalysisAgent())
    engine.register_agent("knowledge_base", KnowledgeBaseAgent())
    return engine
