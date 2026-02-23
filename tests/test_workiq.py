"""Tests for the WorkIQ integration — client, selector, agent, and routing."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agents.workiq_agent import WorkIQAgent
from src.orchestrator.context import ConversationContext
from src.orchestrator.router import AgentType, IntentRouter
from src.workiq.client import WorkIQResult, _extract_sources, _parse_sections
from src.workiq.selector import WorkIQSelector

# ── _parse_sections ─────────────────────────────────────────────────────


class TestParseSections:
    def test_empty_input(self) -> None:
        assert _parse_sections("") == []
        assert _parse_sections("   ") == []

    def test_single_paragraph(self) -> None:
        result = _parse_sections("Hello world")
        assert result == ["Hello world"]

    def test_two_paragraphs(self) -> None:
        result = _parse_sections("First paragraph.\n\nSecond paragraph.")
        assert len(result) == 2
        assert result[0] == "First paragraph."
        assert result[1] == "Second paragraph."

    def test_horizontal_rule_split(self) -> None:
        result = _parse_sections("Above\n---\nBelow")
        assert len(result) == 2
        assert "Above" in result[0]
        assert "Below" in result[1]

    def test_windows_line_endings(self) -> None:
        result = _parse_sections("First\r\n\r\nSecond")
        assert len(result) == 2

    def test_multiple_blank_lines(self) -> None:
        result = _parse_sections("A\n\n\n\nB")
        assert len(result) == 2


# ── _extract_sources ────────────────────────────────────────────────────


class TestExtractSources:
    def test_no_sources(self) -> None:
        assert _extract_sources("No links here.") == []

    def test_single_source(self) -> None:
        result = _extract_sources("See [1](https://example.com) for details.")
        assert result == ["https://example.com"]

    def test_multiple_sources(self) -> None:
        text = "Ref [1](https://a.com) and [2](https://b.com)."
        result = _extract_sources(text)
        assert result == ["https://a.com", "https://b.com"]

    def test_non_numeric_brackets_ignored(self) -> None:
        # Only [n](url) patterns where n is a digit
        result = _extract_sources("[link](https://example.com)")
        assert result == []


# ── WorkIQResult ────────────────────────────────────────────────────────


class TestWorkIQResult:
    def test_ok_when_has_content(self) -> None:
        r = WorkIQResult(query="q", content="answer")
        assert r.ok is True

    def test_not_ok_when_error(self) -> None:
        r = WorkIQResult(query="q", error="something broke")
        assert r.ok is False

    def test_not_ok_when_empty_content(self) -> None:
        r = WorkIQResult(query="q", content="")
        assert r.ok is False

    def test_defaults(self) -> None:
        r = WorkIQResult(query="hello")
        assert r.content == ""
        assert r.sections == []
        assert r.sources == []
        assert r.error == ""


# ── WorkIQSelector ──────────────────────────────────────────────────────


class TestWorkIQSelector:
    def test_auto_resolve_empty_sections(self) -> None:
        selector = WorkIQSelector()
        result = WorkIQResult(query="q", content="", sections=[])
        req = selector.prepare(result, "req-1")
        assert req.resolved is True
        assert req.selected_indices == []

    def test_auto_resolve_single_section(self) -> None:
        selector = WorkIQSelector()
        result = WorkIQResult(query="q", content="answer", sections=["answer"])
        req = selector.prepare(result, "req-2")
        assert req.resolved is True
        assert req.selected_indices == [0]

    def test_multi_section_not_auto_resolved(self) -> None:
        selector = WorkIQSelector()
        result = WorkIQResult(
            query="q",
            content="a\n\nb",
            sections=["Section A", "Section B"],
        )
        req = selector.prepare(result, "req-3")
        assert req.resolved is False
        assert len(req.options) == 2

    def test_resolve_sets_indices(self) -> None:
        selector = WorkIQSelector()
        result = WorkIQResult(
            query="q",
            content="a\n\nb",
            sections=["Section A", "Section B"],
        )
        selector.prepare(result, "req-4")
        ok = selector.resolve("req-4", [1])
        assert ok is True
        content = selector.selected_content("req-4")
        assert content == "Section B"

    def test_resolve_unknown_request(self) -> None:
        selector = WorkIQSelector()
        assert selector.resolve("nonexistent", [0]) is False

    def test_pending_requests(self) -> None:
        selector = WorkIQSelector()
        result = WorkIQResult(
            query="q",
            content="a\n\nb",
            sections=["Section A", "Section B"],
        )
        selector.prepare(result, "req-5")
        pending = selector.pending_requests()
        assert len(pending) == 1
        assert pending[0]["request_id"] == "req-5"

    def test_cleanup_removes_request(self) -> None:
        selector = WorkIQSelector()
        result = WorkIQResult(
            query="q",
            content="a\n\nb",
            sections=["Section A", "Section B"],
        )
        selector.prepare(result, "req-6")
        selector.resolve("req-6", [0])
        selector.cleanup("req-6")
        assert selector.pending_requests() == []
        assert selector.selected_content("req-6") == ""

    def test_resolve_invalid_indices_uses_all(self) -> None:
        selector = WorkIQSelector()
        result = WorkIQResult(
            query="q",
            content="a\n\nb",
            sections=["Section A", "Section B"],
        )
        selector.prepare(result, "req-7")
        selector.resolve("req-7", [99, -1])
        content = selector.selected_content("req-7")
        assert "Section A" in content
        assert "Section B" in content

    @pytest.mark.asyncio
    async def test_wait_for_selection_already_resolved(self) -> None:
        selector = WorkIQSelector()
        result = WorkIQResult(query="q", sections=["A", "B"], content="A\n\nB")
        selector.prepare(result, "req-8")
        selector.resolve("req-8", [0])
        req = await selector.wait_for_selection("req-8")
        assert req.resolved is True

    @pytest.mark.asyncio
    async def test_wait_for_selection_timeout_uses_all(self) -> None:
        selector = WorkIQSelector(timeout=0.1)
        result = WorkIQResult(query="q", sections=["A", "B"], content="A\n\nB")
        selector.prepare(result, "req-9")
        req = await selector.wait_for_selection("req-9")
        assert req.resolved is True
        assert req.selected_indices == [0, 1]  # fail-open

    def test_preview_truncated(self) -> None:
        selector = WorkIQSelector()
        long_section = "x" * 200
        result = WorkIQResult(query="q", sections=[long_section, "short"], content="x")
        req = selector.prepare(result, "req-10")
        assert len(req.options[0].preview) <= 121  # 120 + "…"
        assert req.options[0].preview.endswith("…")


# ── WorkIQAgent ─────────────────────────────────────────────────────────


class TestWorkIQAgent:
    def _make_agent(
        self,
        client: Any = None,
        selector: Any = None,
    ) -> WorkIQAgent:
        return WorkIQAgent(
            agent_id="workiq",
            description="Test WorkIQ",
            client=client,
            selector=selector,
        )

    @pytest.mark.asyncio
    async def test_execute_error_result(self) -> None:
        client = MagicMock()
        client.ask = AsyncMock(return_value=WorkIQResult(
            query="who is my manager?",
            error="workiq CLI not found",
        ))
        agent = self._make_agent(client=client)
        ctx = ConversationContext()

        result = await agent.execute("who is my manager?", ctx)
        assert result.confidence == 0.0
        assert "failed" in result.content.lower()

    @pytest.mark.asyncio
    async def test_execute_single_section_auto_resolved(self) -> None:
        wiq_result = WorkIQResult(
            query="who is my manager?",
            content="Your manager is **Alice**.",
            sections=["Your manager is **Alice**."],
            sources=["https://graph.microsoft.com"],
        )
        client = MagicMock()
        client.ask = AsyncMock(return_value=wiq_result)

        selector = WorkIQSelector()
        agent = self._make_agent(client=client, selector=selector)
        ctx = ConversationContext()

        result = await agent.execute("who is my manager?", ctx)
        assert result.confidence == 0.8
        assert "Alice" in result.content
        assert result.artifacts["workiq_query"] == "who is my manager?"

    @pytest.mark.asyncio
    async def test_execute_multi_section_with_selection(self) -> None:
        wiq_result = WorkIQResult(
            query="my meetings",
            content="Meeting A\n\nMeeting B",
            sections=["Meeting A is at 10am", "Meeting B is at 2pm"],
        )
        client = MagicMock()
        client.ask = AsyncMock(return_value=wiq_result)

        selector = WorkIQSelector(timeout=0.5)
        agent = self._make_agent(client=client, selector=selector)
        ctx = ConversationContext()

        # Run agent in a task so we can resolve the selection concurrently
        async def run_agent():
            return await agent.execute("my meetings", ctx)

        async def resolve_after_delay():
            await asyncio.sleep(0.05)
            # Find the pending request
            pending = selector.pending_requests()
            assert len(pending) == 1
            selector.resolve(pending[0]["request_id"], [1])

        agent_result, _ = await asyncio.gather(
            run_agent(),
            resolve_after_delay(),
        )

        assert agent_result.confidence == 0.8
        assert "Meeting B" in agent_result.content
        assert "Meeting A" not in agent_result.content

    @pytest.mark.asyncio
    async def test_execute_timeout_uses_all(self) -> None:
        wiq_result = WorkIQResult(
            query="my docs",
            content="Doc A\n\nDoc B",
            sections=["Doc A content", "Doc B content"],
        )
        client = MagicMock()
        client.ask = AsyncMock(return_value=wiq_result)

        selector = WorkIQSelector(timeout=0.1)
        agent = self._make_agent(client=client, selector=selector)
        ctx = ConversationContext()

        result = await agent.execute("my docs", ctx)
        # Timeout → fail-open → all content used
        assert result.confidence == 0.8
        assert "Doc A" in result.content
        assert "Doc B" in result.content


# ── Router WorkIQ keywords ─────────────────────────────────────────────


class TestWorkIQRouting:
    @pytest.fixture
    def router(self) -> IntentRouter:
        return IntentRouter()

    def test_workiq_keyword(self, router: IntentRouter) -> None:
        result = router.route_by_keywords("query workiq for my schedule")
        assert result.primary_agent == AgentType.WORKIQ

    def test_m365_keyword(self, router: IntentRouter) -> None:
        result = router.route_by_keywords("check m365 for team info")
        assert result.primary_agent == AgentType.WORKIQ

    def test_who_is_my_pattern(self, router: IntentRouter) -> None:
        result = router.route_by_keywords("who is my manager")
        assert result.primary_agent == AgentType.WORKIQ

    def test_my_team_pattern(self, router: IntentRouter) -> None:
        result = router.route_by_keywords("show my team members")
        assert result.primary_agent == AgentType.WORKIQ

    def test_calendar_keyword(self, router: IntentRouter) -> None:
        result = router.route_by_keywords("what's on my calendar tomorrow")
        assert result.primary_agent == AgentType.WORKIQ

    def test_sharepoint_keyword(self, router: IntentRouter) -> None:
        result = router.route_by_keywords("check sharepoint for the latest files")
        assert result.primary_agent == AgentType.WORKIQ

    def test_meetings_keyword(self, router: IntentRouter) -> None:
        result = router.route_by_keywords("list my meetings for today")
        assert result.primary_agent == AgentType.WORKIQ

    def test_onedrive_keyword(self, router: IntentRouter) -> None:
        result = router.route_by_keywords("show files in onedrive")
        assert result.primary_agent == AgentType.WORKIQ
