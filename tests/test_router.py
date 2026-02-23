"""Tests for the intent router."""

import pytest

from src.orchestrator.router import AgentType, IntentRouter, RoutingKeywordHint


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


class TestExtractRoutingKeywords:
    def test_no_keywords_in_text(self, router: IntentRouter) -> None:
        hints = router.extract_routing_keywords("nothing relevant here")
        assert hints == []

    def test_extracts_log_keywords(self, router: IntentRouter) -> None:
        text = "The error log showed a stack trace with 500 status code."
        hints = router.extract_routing_keywords(text)
        agent_ids = {h.agent_id for h in hints}
        assert AgentType.LOG_ANALYSIS in agent_ids

    def test_extracts_security_keywords(self, router: IntentRouter) -> None:
        text = "A critical security vulnerability was discovered in the CVE database."
        hints = router.extract_routing_keywords(text)
        agent_ids = {h.agent_id for h in hints}
        assert AgentType.SECURITY_SENTINEL in agent_ids

    def test_extracts_multiple_agent_keywords(self, router: IntentRouter) -> None:
        text = "Fix the security vulnerability in the code function."
        hints = router.extract_routing_keywords(text)
        agent_ids = {h.agent_id for h in hints}
        assert len(agent_ids) >= 2

    def test_deduplicates_same_keyword(self, router: IntentRouter) -> None:
        text = "logs and more logs and even more logs"
        hints = router.extract_routing_keywords(text)
        log_hints = [h for h in hints if h.agent_id == AgentType.LOG_ANALYSIS and h.keyword.lower() == "logs"]
        assert len(log_hints) == 1  # deduplicated

    def test_matched_text_has_context(self, router: IntentRouter) -> None:
        text = "The production error log showed multiple failures at midnight."
        hints = router.extract_routing_keywords(text)
        for h in hints:
            assert len(h.matched_text) > len(h.keyword)


class TestRouteWithContext:
    def test_no_enrichment_same_as_keywords(self, router: IntentRouter) -> None:
        msg = "Analyze the error logs"
        baseline = router.route_by_keywords(msg)
        enriched = router.route_with_context(msg, enrichment_hints=None)
        assert enriched.primary_agent == baseline.primary_agent
        assert enriched.enrichment_applied is False

    def test_enrichment_boosts_agent(self, router: IntentRouter) -> None:
        msg = "help me understand the situation"
        hints = [
            RoutingKeywordHint(
                agent_id=AgentType.SECURITY_SENTINEL,
                keyword="security",
                matched_text="security context",
            ),
            RoutingKeywordHint(
                agent_id=AgentType.SECURITY_SENTINEL,
                keyword="vulnerability",
                matched_text="vulnerability alert",
            ),
        ]
        result = router.route_with_context(msg, enrichment_hints=hints)
        assert result.primary_agent == AgentType.SECURITY_SENTINEL
        assert result.enrichment_applied is True
        assert "WorkIQ boost" in result.reasoning

    def test_enrichment_combined_with_message_keywords(self, router: IntentRouter) -> None:
        msg = "fix the issue"
        hints = [
            RoutingKeywordHint(agent_id=AgentType.LOG_ANALYSIS, keyword="error log", matched_text="error log context"),
        ]
        result = router.route_with_context(msg, enrichment_hints=hints)
        # "fix" matches remediation, "error log" hint boosts log_analysis
        all_agents = [result.primary_agent] + result.secondary_agents
        assert len(all_agents) >= 2
        assert result.enrichment_applied is True

    def test_no_match_with_enrichment_defaults(self, router: IntentRouter) -> None:
        msg = "hello"
        result = router.route_with_context(msg, enrichment_hints=[])
        assert result.primary_agent == AgentType.KNOWLEDGE_BASE
        assert result.enrichment_applied is False


class TestLLMPromptWithContext:
    def test_includes_enrichment_text(self, router: IntentRouter) -> None:
        prompt = router.get_llm_routing_prompt_with_context(
            "Fix the bug",
            "Organisational context: The security team flagged CVE-2024-001",
        )
        assert "Organisational context" in prompt
        assert "CVE-2024-001" in prompt
        assert "primary_agent" in prompt
