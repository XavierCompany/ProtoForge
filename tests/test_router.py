"""Tests for the intent router."""

import pytest

from src.orchestrator.router import AgentType, IntentRouter


@pytest.fixture
def router() -> IntentRouter:
    return IntentRouter()


class TestKeywordRouting:
    def test_plan_keywords(self, router: IntentRouter) -> None:
        result = router.route_by_keywords("Create a plan to migrate the database")
        assert result.primary_agent == AgentType.PLAN

    def test_log_analysis_keywords(self, router: IntentRouter) -> None:
        result = router.route_by_keywords("Analyze the error logs from production")
        assert result.primary_agent == AgentType.LOG_ANALYSIS

    def test_code_research_keywords(self, router: IntentRouter) -> None:
        result = router.route_by_keywords("Find the function that handles user authentication")
        assert result.primary_agent == AgentType.CODE_RESEARCH

    def test_remediation_keywords(self, router: IntentRouter) -> None:
        result = router.route_by_keywords("Fix the null pointer exception in UserService")
        assert result.primary_agent == AgentType.REMEDIATION

    def test_knowledge_base_keywords(self, router: IntentRouter) -> None:
        result = router.route_by_keywords("How to configure CORS in the API?")
        assert result.primary_agent == AgentType.KNOWLEDGE_BASE

    def test_data_analysis_keywords(self, router: IntentRouter) -> None:
        result = router.route_by_keywords("Analyze the API latency metrics trend")
        assert result.primary_agent == AgentType.DATA_ANALYSIS

    def test_security_keywords(self, router: IntentRouter) -> None:
        result = router.route_by_keywords("Scan for security vulnerabilities in dependencies")
        assert result.primary_agent == AgentType.SECURITY_SENTINEL

    def test_no_match_defaults_to_knowledge_base(self, router: IntentRouter) -> None:
        result = router.route_by_keywords("hello world")
        assert result.primary_agent == AgentType.KNOWLEDGE_BASE
        assert result.confidence == pytest.approx(0.3)

    def test_multi_agent_routing(self, router: IntentRouter) -> None:
        result = router.route_by_keywords("Fix the security vulnerability in the auth code")
        # Should match remediation, security, and code research
        all_agents = [result.primary_agent] + result.secondary_agents
        assert len(all_agents) >= 2

    def test_confidence_score(self, router: IntentRouter) -> None:
        result = router.route_by_keywords("Analyze the error logs from production")
        assert 0.0 < result.confidence <= 1.0

    def test_llm_routing_prompt(self, router: IntentRouter) -> None:
        prompt = router.get_llm_routing_prompt("Fix the login bug")
        assert "primary_agent" in prompt
        assert "remediation" in prompt.lower()
