"""Shared test fixtures and configuration for the ProtoForge test suite.

Fixture Architecture
--------------------
All pytest fixtures live here so every ``test_*.py`` module can share the
same agent instances and pre-wired engine configurations.

Agent fixtures (standalone):
    plan_agent          — PlanAgent instance (coordinator)
    sub_plan_agent      — SubPlanAgent instance (resource decomposer)
    github_tracker_agent — GitHubTrackerAgent instance (commit/issue docs)
    log_analysis_agent  — LogAnalysisAgent instance (log parsing)
    knowledge_base_agent — KnowledgeBaseAgent instance (doc retrieval)

Engine fixtures (composed):
    base_engine         — 3-agent engine (plan + log_analysis + knowledge_base)
                          No HITL gate — used for simple pipeline tests.
    engine_with_sub_plan — 4-agent engine (plan + sub_plan + log_analysis +
                          knowledge_base) with PlanSelector HITL gate
                          (0.5 s timeout) — used for HITL pipeline tests.

LLM isolation:
    _mock_llm           — autouse fixture that patches BaseAgent._call_llm
                          to return None for every test NOT marked ``live``.
                          This prevents unit tests from making real Azure
                          API calls (which would hang or fail).  Only
                          ``@pytest.mark.live`` tests hit the real endpoint.

How to extend:
    Add new agent fixtures here and register them in the engine fixtures
    if they should participate in standard pipeline tests.  Keep the fixture
    scope at function-level (the default) to guarantee test isolation.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.agents.github_tracker_agent import GitHubTrackerAgent
from src.agents.knowledge_base_agent import KnowledgeBaseAgent
from src.agents.log_analysis_agent import LogAnalysisAgent
from src.agents.plan_agent import PlanAgent
from src.agents.sub_plan_agent import SubPlanAgent
from src.orchestrator.engine import OrchestratorEngine
from src.orchestrator.plan_selector import PlanSelector

# ── LLM isolation ─────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _mock_llm(request: pytest.FixtureRequest) -> Any:  # noqa: ANN401
    """Patch ``BaseAgent._call_llm`` → ``None`` for every non-live test.

    Tests decorated with ``@pytest.mark.live`` are excluded so they can
    exercise the real Azure OpenAI endpoint.  ``test_llm.py`` is also
    excluded because it manages its own granular LLM mocks.  All other
    tests get the heuristic / fallback path, which is exactly what the
    original test suite was written against.
    """
    markers = {m.name for m in request.node.iter_markers()}
    module = getattr(request.node, "module", None)
    module_name = getattr(module, "__name__", "")

    # Skip: live tests hit real Azure; test_llm manages its own LLM mocks
    if "live" in markers or module_name.endswith("test_llm"):
        yield
        return

    mock = AsyncMock(return_value=None)
    with patch("src.agents.base.BaseAgent._call_llm", mock):
        yield


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
