"""Tests for the Governance Guardian system.

Covers:
- GovernanceGuardian: context window thresholds, skill cap enforcement,
  architectural auditing, alert lifecycle
- GovernanceSelector: HITL reviews for context window and skill cap
- Engine integration: governance hooks in _dispatch
- Server endpoints: governance REST API
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.forge.context_budget import ContextBudgetManager
from src.forge.loader import AgentManifest
from src.governance.guardian import (
    ContextWindowExceededError,
    GovernanceAlert,
    GovernanceCategory,
    GovernanceGuardian,
    GovernanceLevel,
    SkillSplitSuggestion,
)
from src.governance.selector import (
    GovernanceSelector,
)
from src.orchestrator.context import ConversationContext

# ── Helpers ─────────────────────────────────────────────────────────────────


def _make_config(
    warning: int = 120_000,
    hard_cap: int = 128_000,
    max_skills: int = 4,
) -> dict[str, Any]:
    return {
        "governance": {
            "context_window": {
                "warning_threshold": warning,
                "hard_cap": hard_cap,
                "check_before_dispatch": True,
                "check_after_dispatch": True,
            },
            "skill_cap": {
                "max_skills_per_agent": max_skills,
                "allow_override": True,
            },
            "hitl": {
                "timeout_seconds": 1,
                "auto_resolve_action": "accept",
            },
        },
    }


def _make_manifest(
    agent_id: str = "test_agent",
    skills: list[str] | None = None,
    subagents: list[str] | None = None,
    context_budget: dict[str, Any] | None = None,
) -> AgentManifest:
    return AgentManifest(
        id=agent_id,
        name=f"Test {agent_id}",
        type="specialist",
        version="1.0.0",
        description="A test agent",
        skills=skills or [],
        subagents=subagents or [],
        context_budget=context_budget or {},
    )


def _make_budget_manager() -> ContextBudgetManager:
    return ContextBudgetManager(
        {
            "token_counting": {
                "method": "character_estimate",
            }
        }
    )


def _attach_governance_methods(orchestrator: MagicMock, guardian: GovernanceGuardian | None) -> None:
    orchestrator.get_governance_report = MagicMock(
        side_effect=lambda: guardian.governance_report() if guardian is not None else None
    )
    orchestrator.get_unresolved_governance_alerts = MagicMock(
        side_effect=lambda: guardian.unresolved_alerts() if guardian is not None else []
    )
    orchestrator.resolve_governance_alert = MagicMock(
        side_effect=lambda alert_id, resolution="accepted": (
            guardian.resolve_alert(alert_id, resolution) if guardian is not None else False
        )
    )


# ═══════════════════════════════════════════════════════════════════════════
# GovernanceGuardian tests
# ═══════════════════════════════════════════════════════════════════════════


class TestGovernanceGuardianInit:
    """Test guardian initialisation and config parsing."""

    def test_default_thresholds(self):
        g = GovernanceGuardian()
        assert g.warning_threshold == 120_000
        assert g.hard_cap == 128_000
        assert g.max_skills == 4

    def test_custom_thresholds(self):
        g = GovernanceGuardian(config=_make_config(warning=100_000, hard_cap=110_000, max_skills=3))
        assert g.warning_threshold == 100_000
        assert g.hard_cap == 110_000
        assert g.max_skills == 3

    def test_with_budget_manager(self):
        bm = _make_budget_manager()
        g = GovernanceGuardian(config=_make_config(), budget_manager=bm)
        assert g._budget_manager is bm

    def test_initial_state(self):
        g = GovernanceGuardian()
        assert g.cumulative_tokens == 0
        assert g.alerts == []
        assert g.agent_token_usage() == {}


class TestGovernanceCountTokens:
    """Test the public count_tokens() facade (P0-3)."""

    def test_count_tokens_with_budget_manager(self):
        bm = _make_budget_manager()
        g = GovernanceGuardian(config=_make_config(), budget_manager=bm)
        count = g.count_tokens("hello world")
        assert count == bm.count_tokens("hello world")

    def test_count_tokens_without_budget_manager(self):
        g = GovernanceGuardian()
        count = g.count_tokens("hello world")
        expected = max(1, len("hello world") // 4)
        assert count == expected

    def test_count_tokens_empty_string(self):
        g = GovernanceGuardian()
        assert g.count_tokens("") == 1  # max(1, 0//4)


class TestContextWindowEnforcement:
    """Test context window threshold detection."""

    def test_healthy_no_alert(self):
        g = GovernanceGuardian(config=_make_config(warning=120_000))
        alert = g.check_context_window("agent_a", 50_000)
        assert alert is None

    def test_warning_threshold_triggers(self):
        g = GovernanceGuardian(config=_make_config(warning=120_000))
        # Pre-load some usage
        g.record_agent_usage("agent_a", 100_000)
        alert = g.check_context_window("agent_b", 21_000)
        assert alert is not None
        assert alert.level == GovernanceLevel.WARNING
        assert alert.category == GovernanceCategory.CONTEXT_WINDOW
        assert "120,000" in alert.message

    def test_hard_cap_triggers(self):
        g = GovernanceGuardian(config=_make_config(hard_cap=128_000))
        g.record_agent_usage("agent_a", 120_000)
        with pytest.raises(ContextWindowExceededError) as exc_info:
            g.check_context_window("agent_b", 10_000)
        assert "hard cap" in exc_info.value.alert.message.lower()
        assert exc_info.value.alert.level == GovernanceLevel.CRITICAL

    def test_exactly_at_warning_threshold(self):
        g = GovernanceGuardian(config=_make_config(warning=120_000))
        g.record_agent_usage("prev", 119_999)
        alert = g.check_context_window("next", 1)
        assert alert is not None  # 119_999 + 1 = 120_000 — AT threshold (>= triggers)

    def test_one_over_warning_threshold(self):
        g = GovernanceGuardian(config=_make_config(warning=120_000))
        g.record_agent_usage("prev", 120_000)
        alert = g.check_context_window("next", 1)
        assert alert is not None
        assert alert.level == GovernanceLevel.WARNING

    def test_warning_stores_context_suggestion_by_alert_id(self):
        g = GovernanceGuardian(config=_make_config(warning=100, hard_cap=200))
        g.record_agent_usage("prev", 90)
        alert = g.check_context_window("next", 15)
        assert alert is not None
        suggestion = g.get_context_suggestion(alert.alert_id)
        assert suggestion is not None
        assert suggestion.current_tokens == 105
        assert suggestion.recommended_split_agent == "next_overflow"

    def test_hard_cap_stores_context_suggestion_by_alert_id(self):
        g = GovernanceGuardian(config=_make_config(warning=10, hard_cap=50))
        g.record_agent_usage("prev", 49)
        with pytest.raises(ContextWindowExceededError) as exc_info:
            g.check_context_window("next", 2)
        alert = exc_info.value.alert
        suggestion = g.get_context_suggestion(alert.alert_id)
        assert suggestion is not None
        assert suggestion.current_tokens == 51

    def test_check_disabled(self):
        config = _make_config()
        config["governance"]["context_window"]["check_before_dispatch"] = False
        g = GovernanceGuardian(config=config)
        g.record_agent_usage("agent_a", 200_000)
        alert = g.check_context_window("agent_b", 1)
        assert alert is None

    def test_cumulative_tracking(self):
        g = GovernanceGuardian(config=_make_config())
        g.record_agent_usage("a", 10_000)
        g.record_agent_usage("b", 20_000)
        g.record_agent_usage("a", 5_000)
        assert g.cumulative_tokens == 35_000
        usage = g.agent_token_usage()
        assert usage["a"] == 15_000
        assert usage["b"] == 20_000

    def test_reset_run(self):
        g = GovernanceGuardian(config=_make_config())
        g.record_agent_usage("a", 100_000)
        g.reset_run()
        assert g.cumulative_tokens == 0
        assert g.agent_token_usage() == {}

    @pytest.mark.asyncio
    async def test_reset_run_is_task_local(self):
        g = GovernanceGuardian(config=_make_config())
        gate = asyncio.Event()

        async def _worker(tokens: int) -> int:
            g.reset_run()
            g.record_agent_usage("agent", tokens)
            await gate.wait()
            return g.cumulative_tokens

        task_a = asyncio.create_task(_worker(111))
        task_b = asyncio.create_task(_worker(222))
        await asyncio.sleep(0)
        gate.set()
        values = sorted(await asyncio.gather(task_a, task_b))
        assert values == [111, 222]


class TestSkillCapEnforcement:
    """Test skill cap validation on manifests."""

    def test_within_cap_no_alert(self):
        g = GovernanceGuardian(config=_make_config(max_skills=4))
        manifest = _make_manifest(skills=["s1", "s2", "s3", "s4"])
        alert = g.validate_skill_cap(manifest)
        assert alert is None

    def test_under_cap_no_alert(self):
        g = GovernanceGuardian(config=_make_config(max_skills=4))
        manifest = _make_manifest(skills=["s1", "s2"])
        alert = g.validate_skill_cap(manifest)
        assert alert is None

    def test_empty_skills_no_alert(self):
        g = GovernanceGuardian(config=_make_config(max_skills=4))
        manifest = _make_manifest(skills=[])
        alert = g.validate_skill_cap(manifest)
        assert alert is None

    def test_over_cap_triggers_alert(self):
        g = GovernanceGuardian(config=_make_config(max_skills=4))
        manifest = _make_manifest(
            agent_id="overloaded",
            skills=["s1", "s2", "s3", "s4", "s5"],
        )
        alert = g.validate_skill_cap(manifest)
        assert alert is not None
        assert alert.level == GovernanceLevel.WARNING
        assert alert.category == GovernanceCategory.SKILL_CAP
        assert "overloaded" in alert.message
        assert "5 skills" in alert.message

    def test_over_cap_correct_suggestion(self):
        g = GovernanceGuardian(config=_make_config(max_skills=4))
        manifest = _make_manifest(
            agent_id="heavy",
            skills=["a", "b", "c", "d", "e", "f"],
        )
        alert = g.validate_skill_cap(manifest)
        assert alert is not None
        assert alert.details["keep_skills"] == ["a", "b", "c", "d"]
        assert alert.details["overflow_skills"] == ["e", "f"]
        assert alert.details["suggested_subagent_id"] == "heavy_overflow"

    def test_over_cap_stores_violation(self):
        g = GovernanceGuardian(config=_make_config(max_skills=4))
        manifest = _make_manifest(agent_id="test", skills=["a", "b", "c", "d", "e"])
        g.validate_skill_cap(manifest)
        violation = g.get_skill_violation("test")
        assert violation is not None
        assert isinstance(violation, SkillSplitSuggestion)
        assert violation.suggested_subagent_id == "test_overflow"


class TestArchitecturalAudit:
    """Test audit_manifest for architectural principle checks."""

    def test_clean_manifest_no_alerts(self):
        g = GovernanceGuardian(config=_make_config())
        manifest = _make_manifest(skills=["s1", "s2"])
        alerts = g.audit_manifest(manifest)
        assert alerts == []

    def test_large_budget_without_subagents(self):
        g = GovernanceGuardian(config=_make_config())
        manifest = _make_manifest(
            context_budget={"max_input_tokens": 80_000},
            subagents=[],
        )
        alerts = g.audit_manifest(manifest)
        assert len(alerts) == 1
        assert alerts[0].category == GovernanceCategory.ARCHITECTURE
        assert "sub-agent" in alerts[0].suggestion.lower()

    def test_large_budget_with_subagents_no_alert(self):
        g = GovernanceGuardian(config=_make_config())
        manifest = _make_manifest(
            context_budget={"max_input_tokens": 80_000},
            subagents=["helper_sub"],
        )
        alerts = g.audit_manifest(manifest)
        assert alerts == []

    def test_combined_skill_cap_and_architecture(self):
        g = GovernanceGuardian(config=_make_config(max_skills=2))
        manifest = _make_manifest(
            skills=["s1", "s2", "s3"],
            context_budget={"max_input_tokens": 70_000},
        )
        alerts = g.audit_manifest(manifest)
        assert len(alerts) == 2
        categories = {a.category for a in alerts}
        assert GovernanceCategory.SKILL_CAP in categories
        assert GovernanceCategory.ARCHITECTURE in categories


class TestAlertLifecycle:
    """Test alert creation, querying, and resolution."""

    def test_alerts_accumulate(self):
        g = GovernanceGuardian(config=_make_config(max_skills=2))
        m1 = _make_manifest(agent_id="a1", skills=["s1", "s2", "s3"])
        m2 = _make_manifest(agent_id="a2", skills=["s1", "s2", "s3"])
        g.validate_skill_cap(m1)
        g.validate_skill_cap(m2)
        assert len(g.alerts) == 2
        assert len(g.unresolved_alerts()) == 2

    def test_resolve_alert(self):
        g = GovernanceGuardian(config=_make_config(max_skills=2))
        m = _make_manifest(skills=["s1", "s2", "s3"])
        alert = g.validate_skill_cap(m)
        ok = g.resolve_alert(alert.alert_id, "accepted")
        assert ok is True
        assert len(g.unresolved_alerts()) == 0

    def test_resolve_alert_cleans_context_suggestion(self):
        g = GovernanceGuardian(config=_make_config(warning=100, hard_cap=200))
        g.record_agent_usage("prev", 90)
        alert = g.check_context_window("next", 15)
        assert alert is not None
        assert g.get_context_suggestion(alert.alert_id) is not None
        ok = g.resolve_alert(alert.alert_id, "accepted")
        assert ok is True
        assert g.get_context_suggestion(alert.alert_id) is None

    def test_resolve_nonexistent_alert(self):
        g = GovernanceGuardian()
        ok = g.resolve_alert("nonexistent", "test")
        assert ok is False

    def test_resolve_already_resolved(self):
        g = GovernanceGuardian(config=_make_config(max_skills=2))
        m = _make_manifest(skills=["s1", "s2", "s3"])
        alert = g.validate_skill_cap(m)
        g.resolve_alert(alert.alert_id, "accepted")
        ok = g.resolve_alert(alert.alert_id, "rejected")
        assert ok is False


class TestGovernanceReport:
    """Test the governance status report."""

    def test_empty_report(self):
        g = GovernanceGuardian(config=_make_config())
        report = g.governance_report()
        assert report["cumulative_tokens"] == 0
        assert report["hard_cap"] == 128_000
        assert report["warning_threshold"] == 120_000
        assert report["utilisation_pct"] == 0.0
        assert report["total_alerts"] == 0
        assert report["unresolved_alerts"] == 0

    def test_report_with_usage(self):
        g = GovernanceGuardian(config=_make_config())
        g.record_agent_usage("plan", 32_000)
        g.record_agent_usage("code_research", 16_000)
        report = g.governance_report()
        assert report["cumulative_tokens"] == 48_000
        assert report["utilisation_pct"] == pytest.approx(37.5)
        assert report["agent_usage"]["plan"] == 32_000

    def test_report_with_violations(self):
        g = GovernanceGuardian(config=_make_config(max_skills=2))
        m = _make_manifest(agent_id="v1", skills=["a", "b", "c"])
        g.validate_skill_cap(m)
        report = g.governance_report()
        assert report["total_alerts"] == 1
        assert "v1" in report["skill_violations"]


# ═══════════════════════════════════════════════════════════════════════════
# GovernanceSelector tests
# ═══════════════════════════════════════════════════════════════════════════


class TestContextWindowReviewSelector:
    """Test HITL for context window threshold breaches."""

    def test_prepare_context_review(self):
        sel = GovernanceSelector(timeout=1.0)
        alert = GovernanceAlert(
            alert_id="gov-0001",
            category=GovernanceCategory.CONTEXT_WINDOW,
            level=GovernanceLevel.WARNING,
            agent_id="test",
            message="Context window warning",
        )
        review = sel.prepare_context_review("req-1", alert)
        assert review.request_id == "req-1"
        assert review.alert is alert
        assert not review.resolved

    def test_resolve_context_review_accepted(self):
        sel = GovernanceSelector(timeout=1.0)
        alert = GovernanceAlert(
            alert_id="gov-0001",
            category=GovernanceCategory.CONTEXT_WINDOW,
            level=GovernanceLevel.WARNING,
            agent_id="test",
            message="test",
        )
        sel.prepare_context_review("req-1", alert)
        ok = sel.resolve_context_review("req-1", accepted=True, user_note="Split it")
        assert ok is True
        review = sel.get_context_review("req-1")
        assert review.resolved
        assert review.accepted
        assert review.user_note == "Split it"

    def test_resolve_context_review_rejected(self):
        sel = GovernanceSelector(timeout=1.0)
        alert = GovernanceAlert(
            alert_id="gov-0001",
            category=GovernanceCategory.CONTEXT_WINDOW,
            level=GovernanceLevel.WARNING,
            agent_id="test",
            message="test",
        )
        sel.prepare_context_review("req-1", alert)
        ok = sel.resolve_context_review("req-1", accepted=False)
        assert ok is True
        review = sel.get_context_review("req-1")
        assert not review.accepted

    def test_resolve_nonexistent_returns_false(self):
        sel = GovernanceSelector()
        ok = sel.resolve_context_review("nope", accepted=True)
        assert ok is False

    @pytest.mark.asyncio
    async def test_wait_for_context_review_timeout(self):
        sel = GovernanceSelector(timeout=0.1)
        alert = GovernanceAlert(
            alert_id="gov-0001",
            category=GovernanceCategory.CONTEXT_WINDOW,
            level=GovernanceLevel.WARNING,
            agent_id="test",
            message="test",
        )
        sel.prepare_context_review("req-1", alert)
        review = await sel.wait_for_context_review("req-1")
        assert review.resolved
        assert review.accepted  # fail-open

    @pytest.mark.asyncio
    async def test_wait_for_context_review_resolved_before(self):
        sel = GovernanceSelector(timeout=1.0)
        alert = GovernanceAlert(
            alert_id="gov-0001",
            category=GovernanceCategory.CONTEXT_WINDOW,
            level=GovernanceLevel.WARNING,
            agent_id="test",
            message="test",
        )
        sel.prepare_context_review("req-1", alert)
        sel.resolve_context_review("req-1", accepted=False)
        review = await sel.wait_for_context_review("req-1")
        assert review.resolved
        assert not review.accepted

    @pytest.mark.asyncio
    async def test_wait_for_nonexistent_raises(self):
        sel = GovernanceSelector()
        with pytest.raises(KeyError):
            await sel.wait_for_context_review("nope")

    def test_pending_context_reviews(self):
        sel = GovernanceSelector()
        alert = GovernanceAlert(
            alert_id="gov-0001",
            category=GovernanceCategory.CONTEXT_WINDOW,
            level=GovernanceLevel.WARNING,
            agent_id="test",
            message="msg",
            suggestion="suggest",
        )
        sel.prepare_context_review("req-1", alert)
        pending = sel.pending_context_reviews()
        assert len(pending) == 1
        assert pending[0]["request_id"] == "req-1"
        assert pending[0]["message"] == "msg"

    def test_cleanup_context_review(self):
        sel = GovernanceSelector()
        alert = GovernanceAlert(
            alert_id="gov-0001",
            category=GovernanceCategory.CONTEXT_WINDOW,
            level=GovernanceLevel.WARNING,
            agent_id="test",
            message="msg",
        )
        sel.prepare_context_review("req-1", alert)
        sel.cleanup_context_review("req-1")
        assert sel.pending_context_reviews() == []
        assert sel.get_context_review("req-1") is None


class TestSkillCapReviewSelector:
    """Test HITL for skill cap violations."""

    def _alert(self) -> GovernanceAlert:
        return GovernanceAlert(
            alert_id="gov-0002",
            category=GovernanceCategory.SKILL_CAP,
            level=GovernanceLevel.WARNING,
            agent_id="heavy_agent",
            message="5 skills > 4 max",
            suggestion="Split skills",
        )

    def _split(self) -> SkillSplitSuggestion:
        return SkillSplitSuggestion(
            agent_id="heavy_agent",
            current_skills=["a", "b", "c", "d", "e"],
            keep_skills=["a", "b", "c", "d"],
            overflow_skills=["e"],
            suggested_subagent_id="heavy_agent_overflow",
        )

    def test_prepare_skill_review(self):
        sel = GovernanceSelector()
        review = sel.prepare_skill_review("req-1", self._alert(), self._split())
        assert review.request_id == "req-1"
        assert not review.resolved
        assert review.split_suggestion is not None

    def test_resolve_skill_review_accepted(self):
        sel = GovernanceSelector()
        sel.prepare_skill_review("req-1", self._alert(), self._split())
        ok = sel.resolve_skill_review("req-1", accepted=True)
        assert ok
        review = sel.get_skill_review("req-1")
        assert review.accepted
        assert review.resolved

    def test_resolve_skill_review_with_custom_split(self):
        sel = GovernanceSelector()
        sel.prepare_skill_review("req-1", self._alert(), self._split())
        ok = sel.resolve_skill_review(
            "req-1",
            accepted=True,
            custom_keep=["a", "c"],
            custom_overflow=["b", "d", "e"],
        )
        assert ok
        review = sel.get_skill_review("req-1")
        assert review.custom_keep == ["a", "c"]
        assert review.custom_overflow == ["b", "d", "e"]

    def test_resolve_skill_review_override(self):
        sel = GovernanceSelector()
        sel.prepare_skill_review("req-1", self._alert(), self._split())
        ok = sel.resolve_skill_review("req-1", accepted=False, override=True)
        assert ok
        review = sel.get_skill_review("req-1")
        assert review.overridden
        assert not review.accepted

    def test_resolve_nonexistent_skill(self):
        sel = GovernanceSelector()
        ok = sel.resolve_skill_review("nope", accepted=True)
        assert ok is False

    @pytest.mark.asyncio
    async def test_wait_for_skill_review_timeout(self):
        sel = GovernanceSelector(timeout=0.1)
        sel.prepare_skill_review("req-1", self._alert(), self._split())
        review = await sel.wait_for_skill_review("req-1")
        assert review.resolved
        assert review.accepted  # fail-open

    @pytest.mark.asyncio
    async def test_wait_for_nonexistent_skill_raises(self):
        sel = GovernanceSelector()
        with pytest.raises(KeyError):
            await sel.wait_for_skill_review("nope")

    def test_pending_skill_reviews(self):
        sel = GovernanceSelector()
        sel.prepare_skill_review("req-1", self._alert(), self._split())
        pending = sel.pending_skill_reviews()
        assert len(pending) == 1
        assert pending[0]["request_id"] == "req-1"
        assert "split" in pending[0]
        assert pending[0]["split"]["suggested_subagent_id"] == "heavy_agent_overflow"

    def test_cleanup_skill_review(self):
        sel = GovernanceSelector()
        sel.prepare_skill_review("req-1", self._alert(), self._split())
        sel.cleanup_skill_review("req-1")
        assert sel.pending_skill_reviews() == []


# ═══════════════════════════════════════════════════════════════════════════
# Agent Lifecycle Review Selector tests
# ═══════════════════════════════════════════════════════════════════════════


class TestAgentLifecycleReviewSelector:
    """Test HITL for agent lifecycle actions (disable/remove)."""

    def test_prepare_lifecycle_review_disable(self):
        sel = GovernanceSelector(timeout=1.0)
        review = sel.prepare_lifecycle_review(
            "lc-1",
            "disable",
            "log_analysis",
            ["plan", "code_research"],
        )
        assert review.request_id == "lc-1"
        assert review.action == "disable"
        assert review.target_agent_id == "log_analysis"
        assert review.enabled_agents == ["plan", "code_research"]
        assert not review.resolved

    def test_prepare_lifecycle_review_remove(self):
        sel = GovernanceSelector(timeout=1.0)
        review = sel.prepare_lifecycle_review(
            "lc-2",
            "remove",
            "data_analysis",
            ["plan"],
        )
        assert review.action == "remove"
        assert review.target_agent_id == "data_analysis"

    def test_resolve_lifecycle_review_accepted(self):
        sel = GovernanceSelector(timeout=1.0)
        sel.prepare_lifecycle_review(
            "lc-1",
            "disable",
            "log_analysis",
            ["plan"],
        )
        ok = sel.resolve_lifecycle_review("lc-1", accepted=True, user_note="Go ahead")
        assert ok is True
        review = sel.get_lifecycle_review("lc-1")
        assert review.resolved
        assert review.accepted
        assert review.user_note == "Go ahead"

    def test_resolve_lifecycle_review_rejected(self):
        sel = GovernanceSelector(timeout=1.0)
        sel.prepare_lifecycle_review(
            "lc-1",
            "disable",
            "log_analysis",
            ["plan"],
        )
        ok = sel.resolve_lifecycle_review("lc-1", accepted=False)
        assert ok is True
        review = sel.get_lifecycle_review("lc-1")
        assert not review.accepted

    def test_resolve_nonexistent_returns_false(self):
        sel = GovernanceSelector()
        ok = sel.resolve_lifecycle_review("nope", accepted=True)
        assert ok is False

    @pytest.mark.asyncio
    async def test_wait_for_lifecycle_review_timeout_fail_closed(self):
        """Lifecycle reviews fail-CLOSED on timeout (rejects action)."""
        sel = GovernanceSelector(timeout=0.1)
        sel.prepare_lifecycle_review(
            "lc-1",
            "disable",
            "log_analysis",
            ["plan"],
        )
        review = await sel.wait_for_lifecycle_review("lc-1")
        assert review.resolved
        assert not review.accepted  # fail-CLOSED — opposite of context/skill

    @pytest.mark.asyncio
    async def test_wait_for_lifecycle_review_uses_lifecycle_timeout(self):
        sel = GovernanceSelector(timeout=5.0, lifecycle_timeout=0.1)
        sel.prepare_lifecycle_review(
            "lc-override",
            "disable",
            "log_analysis",
            ["plan"],
        )
        review = await sel.wait_for_lifecycle_review("lc-override")
        assert review.resolved
        assert not review.accepted

    @pytest.mark.asyncio
    async def test_wait_for_lifecycle_review_resolved_before(self):
        sel = GovernanceSelector(timeout=1.0)
        sel.prepare_lifecycle_review(
            "lc-1",
            "remove",
            "log_analysis",
            ["plan"],
        )
        sel.resolve_lifecycle_review("lc-1", accepted=True, user_note="Remove it")
        review = await sel.wait_for_lifecycle_review("lc-1")
        assert review.resolved
        assert review.accepted

    @pytest.mark.asyncio
    async def test_wait_for_nonexistent_lifecycle_raises(self):
        sel = GovernanceSelector()
        with pytest.raises(KeyError):
            await sel.wait_for_lifecycle_review("nope")

    def test_pending_lifecycle_reviews(self):
        sel = GovernanceSelector()
        sel.prepare_lifecycle_review(
            "lc-1",
            "disable",
            "log_analysis",
            ["plan", "code_research"],
        )
        pending = sel.pending_lifecycle_reviews()
        assert len(pending) == 1
        assert pending[0]["request_id"] == "lc-1"
        assert pending[0]["action"] == "disable"
        assert pending[0]["target_agent_id"] == "log_analysis"
        assert pending[0]["enabled_agents_after"] == ["plan", "code_research"]

    def test_cleanup_lifecycle_review(self):
        sel = GovernanceSelector()
        sel.prepare_lifecycle_review(
            "lc-1",
            "disable",
            "log_analysis",
            ["plan"],
        )
        sel.cleanup_lifecycle_review("lc-1")
        assert sel.pending_lifecycle_reviews() == []
        assert sel.get_lifecycle_review("lc-1") is None


# ═══════════════════════════════════════════════════════════════════════════
# Engine integration tests
# ═══════════════════════════════════════════════════════════════════════════


class TestEngineGovernanceIntegration:
    """Test governance hooks inside the OrchestratorEngine."""

    def _make_engine(
        self,
        warning: int = 120_000,
        hard_cap: int = 128_000,
    ):
        from src.orchestrator.engine import OrchestratorEngine

        bm = _make_budget_manager()
        guardian = GovernanceGuardian(
            config=_make_config(warning=warning, hard_cap=hard_cap),
            budget_manager=bm,
        )
        selector = GovernanceSelector(timeout=0.1)
        engine = OrchestratorEngine(
            governance_guardian=guardian,
            governance_selector=selector,
        )
        return engine, guardian, selector

    def _mock_agent(self, agent_id: str = "test"):
        agent = MagicMock()
        agent.agent_id = agent_id
        agent.system_prompt = "You are a test agent."

        async def mock_execute(message, context, params=None):
            from src.orchestrator.context import AgentResult

            return AgentResult(
                agent_id=agent_id,
                content=f"Result from {agent_id}",
                confidence=0.9,
            )

        agent.execute = AsyncMock(side_effect=mock_execute)
        return agent

    @pytest.mark.asyncio
    async def test_dispatch_records_usage(self):
        engine, guardian, _ = self._make_engine()
        agent = self._mock_agent("log_analysis")
        engine.register_agent("log_analysis", agent)

        from src.orchestrator.router import RoutingDecision

        routing = RoutingDecision(
            primary_agent="log_analysis",
            confidence=0.9,
            reasoning="test",
        )
        ctx = ConversationContext()
        await engine._dispatch("log_analysis", "test message", routing, ctx)
        assert guardian.cumulative_tokens > 0

    @pytest.mark.asyncio
    async def test_dispatch_triggers_warning_hitl(self):
        engine, guardian, _selector = self._make_engine(warning=10, hard_cap=200)
        agent = self._mock_agent("agent_a")
        engine.register_agent("agent_a", agent)

        # Pre-load tokens to get close to warning
        guardian.record_agent_usage("prev", 15)

        from src.orchestrator.router import RoutingDecision

        routing = RoutingDecision(
            primary_agent="agent_a",
            confidence=0.9,
            reasoning="test",
        )
        ctx = ConversationContext()
        result = await engine._dispatch("agent_a", "x" * 100, routing, ctx)
        # Should still execute (warning, not hard cap)
        assert result.content.startswith("Result from")
        # Alert should have been created
        assert len(guardian.alerts) > 0

    @pytest.mark.asyncio
    async def test_process_aborts_pipeline_on_plan_hard_cap(self):
        engine, guardian, _selector = self._make_engine(warning=10, hard_cap=50)
        plan_agent = self._mock_agent("plan")
        engine.register_agent("plan", plan_agent)

        # Force hard-cap breach before Plan dispatch.
        guardian.record_agent_usage("prev", 100)

        from src.orchestrator.context import ConversationContext
        from src.orchestrator.router import RoutingDecision

        routing = RoutingDecision(primary_agent="plan", confidence=0.9, reasoning="test")
        ctx = ConversationContext()

        engine._run_sub_plan_pipeline = AsyncMock(  # type: ignore[method-assign]
            side_effect=AssertionError("sub-plan should not run after hard-cap abort")
        )

        response = await engine._process_after_routing("hello", routing, ctx)
        assert "hard cap" in response.lower()
        assert ctx.get_memory("pipeline_phase") == "aborted_hard_cap"
        plan_agent.execute.assert_not_called()
        engine._run_sub_plan_pipeline.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatch_without_governance(self):
        from src.orchestrator.engine import OrchestratorEngine

        engine = OrchestratorEngine()
        agent = self._mock_agent("test")
        engine.register_agent("test", agent)

        from src.orchestrator.router import RoutingDecision

        routing = RoutingDecision(
            primary_agent="test",
            confidence=0.9,
            reasoning="test",
        )
        ctx = ConversationContext()
        result = await engine._dispatch("test", "hello", routing, ctx)
        assert result.content == "Result from test"

    @pytest.mark.asyncio
    async def test_process_with_routing_skips_llm_override_when_forced(self):
        engine, _guardian, _selector = self._make_engine()
        plan_agent = self._mock_agent("plan")
        engine.register_agent("plan", plan_agent)

        from src.orchestrator.router import RoutingDecision

        routing = RoutingDecision(
            primary_agent="plan",
            confidence=0.1,
            reasoning="forced_control_plane",
        )

        async def fail_llm_override(_message: str):
            raise AssertionError("_route_with_llm should not be called for forced routing")

        engine._route_with_llm = fail_llm_override  # type: ignore[method-assign]

        result, ctx = await engine.process_with_routing(
            "document commit",
            routing,
            auto_accept_hitl=True,
        )
        assert "Result from plan" in result
        assert ctx.get_memory("forced_routing") is True

    def test_reset_context_resets_governance(self):
        engine, guardian, _ = self._make_engine()
        guardian.record_agent_usage("plan", 50_000)
        assert guardian.cumulative_tokens == 50_000
        engine.reset_context()
        assert guardian.cumulative_tokens == 0

    def test_get_status_includes_governance(self):
        engine, guardian, _ = self._make_engine()
        guardian.record_agent_usage("plan", 10_000)
        status = engine.get_status()
        assert status["governance_enabled"] is True
        assert "governance" in status
        assert status["governance"]["cumulative_tokens"] == 10_000

    def test_get_agent_returns_registered(self):
        engine, _, _ = self._make_engine()
        agent = self._mock_agent("test")
        engine.register_agent("test", agent)
        assert engine.get_agent("test") is agent

    def test_get_agent_returns_none_for_missing(self):
        engine, _, _ = self._make_engine()
        assert engine.get_agent("nonexistent") is None


class TestEngineAgentLifecycle:
    """Test engine-level disable / enable / unregister with HITL."""

    def _make_engine(self):
        from src.orchestrator.engine import OrchestratorEngine

        bm = _make_budget_manager()
        guardian = GovernanceGuardian(
            config=_make_config(),
            budget_manager=bm,
        )
        selector = GovernanceSelector(timeout=0.1)
        engine = OrchestratorEngine(
            governance_guardian=guardian,
            governance_selector=selector,
            budget_manager=bm,
        )
        return engine, guardian, selector, bm

    def _mock_agent(self, agent_id: str = "test"):
        agent = MagicMock()
        agent.agent_id = agent_id
        agent.system_prompt = "You are a test agent."

        async def mock_execute(message, context, params=None):
            from src.orchestrator.context import AgentResult

            return AgentResult(
                agent_id=agent_id,
                content=f"Result from {agent_id}",
                confidence=0.9,
            )

        agent.execute = AsyncMock(side_effect=mock_execute)
        return agent

    def test_list_enabled_agents(self):
        engine, _, _, _ = self._make_engine()
        engine.register_agent("a", self._mock_agent("a"))
        engine.register_agent("b", self._mock_agent("b"))
        enabled = engine.list_enabled_agents()
        assert sorted(enabled) == ["a", "b"]

    def test_list_disabled_agents_initially_empty(self):
        engine, _, _, _ = self._make_engine()
        assert engine.list_disabled_agents() == []

    @pytest.mark.asyncio
    async def test_enable_agent(self):
        engine, _, _, _ = self._make_engine()
        engine.register_agent("a", self._mock_agent("a"))
        # Manually disable first
        engine._disabled_agents.add("a")
        result = await engine.enable_agent("a")
        assert result["action"] == "enabled"
        assert "a" not in engine._disabled_agents

    @pytest.mark.asyncio
    async def test_enable_nonexistent_returns_error(self):
        engine, _, _, _ = self._make_engine()
        result = await engine.enable_agent("nope")
        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_disable_agent_timeout_rejects(self):
        """HITL timeout for disable should fail-CLOSED (agent stays enabled)."""
        engine, _, _, _ = self._make_engine()
        engine.register_agent("a", self._mock_agent("a"))
        engine.register_agent("b", self._mock_agent("b"))
        result = await engine.disable_agent("a")
        # Timeout → fail-closed → rejected
        assert result["ok"] is False
        assert "a" not in engine._disabled_agents

    @pytest.mark.asyncio
    async def test_disable_agent_accepted(self):
        """When HITL is resolved with accepted=True, agent is disabled."""
        engine, _, selector, _ = self._make_engine()
        engine.register_agent("a", self._mock_agent("a"))
        engine.register_agent("b", self._mock_agent("b"))

        # Pre-resolve the lifecycle review before engine waits
        # We need to peek at the request_id that will be created
        import asyncio

        async def _accept_soon():
            await asyncio.sleep(0.02)
            pending = selector.pending_lifecycle_reviews()
            if pending:
                selector.resolve_lifecycle_review(pending[0]["request_id"], accepted=True)

        # Increase the timeout so the async accept works
        selector._timeout = 2.0
        accept_task = asyncio.create_task(_accept_soon())
        result = await engine.disable_agent("a")
        await accept_task
        assert result["action"] == "disabled"
        assert "a" in engine._disabled_agents

    @pytest.mark.asyncio
    async def test_dispatch_disabled_agent_returns_early(self):
        engine, _, _, _ = self._make_engine()
        agent = self._mock_agent("a")
        engine.register_agent("a", agent)
        engine._disabled_agents.add("a")

        from src.orchestrator.router import RoutingDecision

        routing = RoutingDecision(primary_agent="a", confidence=0.9, reasoning="test")
        ctx = ConversationContext()
        result = await engine._dispatch("a", "hello", routing, ctx)
        assert result.confidence == 0.0
        assert "disabled" in result.content.lower()
        agent.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_unregister_agent_timeout_rejects(self):
        """HITL timeout for unregister should fail-CLOSED (agent stays)."""
        engine, _, _, _ = self._make_engine()
        engine.register_agent("a", self._mock_agent("a"))
        result = await engine.unregister_agent("a")
        assert result["ok"] is False
        assert "a" in engine._agents

    def test_get_status_includes_lifecycle_info(self):
        engine, _, _, _ = self._make_engine()
        engine.register_agent("a", self._mock_agent("a"))
        engine._disabled_agents.add("a")
        status = engine.get_status()
        assert "a" in status["disabled_agents"]
        assert "a" not in status["enabled_agents"]


class TestBudgetEnforcementInDispatch:
    """Test that per-agent budget allocation and truncation fire in _dispatch."""

    def _make_budget_engine(self, warning: int = 120_000, hard_cap: int = 128_000):
        from src.forge.loader import AgentManifest, ForgeRegistry
        from src.orchestrator.engine import OrchestratorEngine

        # Build a minimal ForgeRegistry with a manifest
        registry = ForgeRegistry()
        registry.agents["test_agent"] = AgentManifest(
            id="test_agent",
            name="Test Agent",
            type="specialist",
            version="1.0.0",
            description="Test",
            context_budget={"max_input_tokens": 100, "max_output_tokens": 50, "strategy": "priority"},
        )
        registry.context_config = {
            "scaling": {"max_parallel_agents": 3},
            "governance": {
                "context_window": {
                    "warning_threshold": warning,
                    "hard_cap": hard_cap,
                    "enforce_hard_cap": True,
                    "check_before_dispatch": True,
                    "check_after_dispatch": True,
                },
            },
        }

        bm = _make_budget_manager()
        guardian = GovernanceGuardian(
            config=_make_config(warning=warning, hard_cap=hard_cap),
            budget_manager=bm,
        )
        selector = GovernanceSelector(timeout=0.1)
        engine = OrchestratorEngine(
            governance_guardian=guardian,
            governance_selector=selector,
            budget_manager=bm,
            forge_registry=registry,
        )
        return engine, guardian, bm, registry

    def _mock_agent(self, agent_id: str = "test_agent"):
        agent = MagicMock()
        agent.agent_id = agent_id
        agent.system_prompt = "System."

        async def mock_execute(message, context, params=None):
            from src.orchestrator.context import AgentResult

            return AgentResult(
                agent_id=agent_id,
                content=f"Result from {agent_id}",
                confidence=0.9,
            )

        agent.execute = AsyncMock(side_effect=mock_execute)
        return agent

    @pytest.mark.asyncio
    async def test_dispatch_allocates_budget(self):
        engine, _, bm, _ = self._make_budget_engine()
        agent = self._mock_agent()
        engine.register_agent("test_agent", agent)

        from src.orchestrator.router import RoutingDecision

        routing = RoutingDecision(primary_agent="test_agent", confidence=0.9, reasoning="test")
        ctx = ConversationContext()
        await engine._dispatch("test_agent", "short msg", routing, ctx)

        # Budget should have been allocated for test_agent
        assert "test_agent" in bm._budgets
        assert bm._budgets["test_agent"].max_input == 100
        assert bm._budgets["test_agent"].max_output == 50

    @pytest.mark.asyncio
    async def test_dispatch_truncates_long_input(self):
        engine, _, _, _ = self._make_budget_engine()
        agent = self._mock_agent()
        engine.register_agent("test_agent", agent)

        from src.orchestrator.router import RoutingDecision

        routing = RoutingDecision(primary_agent="test_agent", confidence=0.9, reasoning="test")
        # Send a message that's way over the 100-token input budget (~400 chars at 4 chars/token)
        long_msg = "x" * 2000
        ctx = ConversationContext()
        await engine._dispatch("test_agent", long_msg, routing, ctx)

        # The agent should have been called with a truncated message
        call_args = agent.execute.call_args
        actual_msg = call_args[0][0]
        assert len(actual_msg) < len(long_msg)

    @pytest.mark.asyncio
    async def test_dispatch_records_budget_usage(self):
        engine, _, bm, _ = self._make_budget_engine()
        agent = self._mock_agent()
        engine.register_agent("test_agent", agent)

        from src.orchestrator.router import RoutingDecision

        routing = RoutingDecision(primary_agent="test_agent", confidence=0.9, reasoning="test")
        ctx = ConversationContext()
        await engine._dispatch("test_agent", "hello", routing, ctx)

        # Usage should be recorded
        usage = bm._usage.get("test_agent")
        assert usage is not None
        assert usage["input"] > 0
        assert usage["output"] > 0

    @pytest.mark.asyncio
    async def test_dispatch_aborts_on_hard_cap(self):
        engine, guardian, _, _ = self._make_budget_engine(hard_cap=50)
        agent = self._mock_agent()
        engine.register_agent("test_agent", agent)

        # Pre-fill tokens to hit hard cap
        guardian.record_agent_usage("prev", 100)

        from src.orchestrator.router import RoutingDecision

        routing = RoutingDecision(primary_agent="test_agent", confidence=0.9, reasoning="test")
        ctx = ConversationContext()
        result = await engine._dispatch("test_agent", "hello", routing, ctx)

        # Should abort with hard cap message
        assert "hard cap" in result.content.lower()
        assert result.confidence == 0.0
        # Agent execute should NOT have been called
        agent.execute.assert_not_called()


class TestFanOutCapEnforcement:
    """Test that _resolve_sub_agents enforces the max_parallel_agents cap."""

    def test_fan_out_cap_enforced(self):
        from src.forge.loader import ForgeRegistry
        from src.orchestrator.engine import OrchestratorEngine

        registry = ForgeRegistry()
        registry.context_config = {"scaling": {"max_parallel_agents": 2}}

        engine = OrchestratorEngine(forge_registry=registry)

        from src.orchestrator.router import RoutingDecision

        routing = RoutingDecision(
            primary_agent="log_analysis",
            confidence=0.9,
            reasoning="test",
            secondary_agents=["code_research", "remediation", "security_sentinel"],
        )
        result = engine._resolve_sub_agents(routing)
        assert len(result) == 2
        assert result == ["log_analysis", "code_research"]

    def test_fan_out_under_cap_passes_all(self):
        from src.forge.loader import ForgeRegistry
        from src.orchestrator.engine import OrchestratorEngine

        registry = ForgeRegistry()
        registry.context_config = {"scaling": {"max_parallel_agents": 5}}

        engine = OrchestratorEngine(forge_registry=registry)

        from src.orchestrator.router import RoutingDecision

        routing = RoutingDecision(
            primary_agent="log_analysis",
            confidence=0.9,
            reasoning="test",
            secondary_agents=["code_research"],
        )
        result = engine._resolve_sub_agents(routing)
        assert len(result) == 2

    def test_fan_out_default_cap_is_three(self):
        from src.orchestrator.engine import OrchestratorEngine

        engine = OrchestratorEngine()
        assert engine._max_parallel_agents == 3


class TestHardCapBehavior:
    """Test enforce_hard_cap config flag behaviour."""

    def test_enforce_hard_cap_defaults_true(self):
        g = GovernanceGuardian()
        assert g.enforce_hard_cap is True

    def test_enforce_hard_cap_raises_exception(self):
        config = _make_config(hard_cap=100)
        config["governance"]["context_window"]["enforce_hard_cap"] = True
        g = GovernanceGuardian(config=config)
        g.record_agent_usage("a", 95)
        with pytest.raises(ContextWindowExceededError):
            g.check_context_window("b", 10)

    def test_enforce_hard_cap_disabled_returns_alert(self):
        config = _make_config(hard_cap=100)
        config["governance"]["context_window"]["enforce_hard_cap"] = False
        g = GovernanceGuardian(config=config)
        g.record_agent_usage("a", 95)
        alert = g.check_context_window("b", 10)
        assert alert is not None
        assert alert.level == GovernanceLevel.CRITICAL

    def test_exception_carries_alert(self):
        config = _make_config(hard_cap=50)
        g = GovernanceGuardian(config=config)
        g.record_agent_usage("a", 45)
        with pytest.raises(ContextWindowExceededError) as exc_info:
            g.check_context_window("b", 10)
        assert exc_info.value.alert.category == GovernanceCategory.CONTEXT_WINDOW


class TestBudgetManagerUsageReport:
    """Test the usage_report from ContextBudgetManager."""

    def test_usage_report_after_allocation(self):
        bm = _make_budget_manager()
        bm.allocate("agent_a", "specialist", override={"max_input_tokens": 15000, "max_output_tokens": 7000})
        bm.record_usage("agent_a", 5000, direction="input")
        bm.record_usage("agent_a", 2000, direction="output")
        report = bm.usage_report()
        assert "agent_a" in report
        assert report["agent_a"]["used"]["input"] == 5000
        assert report["agent_a"]["remaining"]["input"] == 10000


# ═══════════════════════════════════════════════════════════════════════════
# ForgeLoader integration tests
# ═══════════════════════════════════════════════════════════════════════════


class TestForgeLoaderGovernanceIntegration:
    """Test governance hooks inside the ForgeLoader."""

    def test_loader_accepts_governance_guardian(self):
        from src.forge.loader import ForgeLoader

        guardian = GovernanceGuardian(config=_make_config(max_skills=4))
        loader = ForgeLoader("forge", governance_guardian=guardian)
        assert loader._governance is guardian

    def test_loader_without_governance(self):
        from src.forge.loader import ForgeLoader

        loader = ForgeLoader("forge")
        assert loader._governance is None


# ═══════════════════════════════════════════════════════════════════════════
# Server endpoint tests
# ═══════════════════════════════════════════════════════════════════════════


class TestGovernanceServerEndpoints:
    """Test governance REST API endpoints via TestClient."""

    @pytest.fixture
    def client(self):
        """Create a test client with governance wired in."""
        from fastapi.testclient import TestClient

        from src.server import create_app

        # Minimal mocks
        orchestrator = MagicMock()
        guardian = GovernanceGuardian(config=_make_config())
        orchestrator._governance = guardian
        _attach_governance_methods(orchestrator, guardian)

        async def _mock_process_with_enrichment(message: str, *, ctx: ConversationContext | None = None):
            run_ctx = ctx or ConversationContext()
            return f"enriched:{message}", run_ctx

        async def _mock_process(message: str, *, ctx: ConversationContext | None = None):
            run_ctx = ctx or ConversationContext()
            return f"standard:{message}", run_ctx

        orchestrator.process = AsyncMock(side_effect=_mock_process)
        orchestrator.process_with_enrichment = AsyncMock(side_effect=_mock_process_with_enrichment)
        orchestrator.get_status.return_value = {
            "session_id": "test",
            "registered_agents": [],
            "message_count": 0,
            "active_workflow": None,
            "provider": "azure_openai",
            "workiq_enrichment_available": False,
            "governance_enabled": True,
            "governance": guardian.governance_report(),
        }

        mcp_server = MagicMock()
        mcp_server.get_status.return_value = {"tools_count": 0}
        catalog = MagicMock()
        catalog.list_agents.return_value = []
        catalog.get_status.return_value = {"installed_skills": 0}
        workflow_engine = MagicMock()
        workflow_engine.list_workflows.return_value = []

        governance_selector = GovernanceSelector(timeout=1.0)

        app = create_app(
            orchestrator=orchestrator,
            mcp_server=mcp_server,
            catalog=catalog,
            workflow_engine=workflow_engine,
            governance_selector=governance_selector,
            require_control_plane_api_key=False,
        )
        return TestClient(app), guardian, governance_selector

    def test_governance_status(self, client):
        test_client, guardian, _ = client
        guardian.record_agent_usage("plan", 10_000)
        resp = test_client.get("/governance/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
        assert data["cumulative_tokens"] == 10_000

    def test_governance_alerts_empty(self, client):
        test_client, _, _ = client
        resp = test_client.get("/governance/alerts")
        assert resp.status_code == 200
        assert resp.json()["alerts"] == []

    def test_governance_alerts_with_violations(self, client):
        test_client, guardian, _ = client
        manifest = _make_manifest(agent_id="heavy", skills=["a", "b", "c", "d", "e"])
        guardian.validate_skill_cap(manifest)
        resp = test_client.get("/governance/alerts")
        assert resp.status_code == 200
        alerts = resp.json()["alerts"]
        assert len(alerts) == 1
        assert alerts[0]["category"] == "skill_cap"

    def test_resolve_alert(self, client):
        test_client, guardian, _ = client
        manifest = _make_manifest(agent_id="heavy", skills=["a", "b", "c", "d", "e"])
        alert = guardian.validate_skill_cap(manifest)
        resp = test_client.post(
            "/governance/resolve-alert",
            json={"alert_id": alert.alert_id, "resolution": "accepted"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "resolved"

    def test_resolve_nonexistent_alert(self, client):
        test_client, _, _ = client
        resp = test_client.post(
            "/governance/resolve-alert",
            json={"alert_id": "nope"},
        )
        assert resp.status_code == 404

    def test_context_reviews_empty(self, client):
        test_client, _, _ = client
        resp = test_client.get("/governance/context-reviews")
        assert resp.status_code == 200
        assert resp.json()["pending"] == []

    def test_context_review_lifecycle(self, client):
        test_client, _, governance_selector = client
        alert = GovernanceAlert(
            alert_id="gov-0001",
            category=GovernanceCategory.CONTEXT_WINDOW,
            level=GovernanceLevel.WARNING,
            agent_id="test",
            message="Threshold crossed",
            suggestion="Decompose task",
        )
        governance_selector.prepare_context_review("req-1", alert)

        # Check pending
        resp = test_client.get("/governance/context-reviews")
        assert len(resp.json()["pending"]) == 1

        # Resolve
        resp = test_client.post(
            "/governance/context-reviews/resolve",
            json={"request_id": "req-1", "accepted": True, "user_note": "Split it"},
        )
        assert resp.status_code == 200
        assert resp.json()["accepted"] is True

    def test_resolve_nonexistent_context_review(self, client):
        test_client, _, _ = client
        resp = test_client.post(
            "/governance/context-reviews/resolve",
            json={"request_id": "nope", "accepted": True},
        )
        assert resp.status_code == 404

    def test_skill_reviews_empty(self, client):
        test_client, _, _ = client
        resp = test_client.get("/governance/skill-reviews")
        assert resp.status_code == 200
        assert resp.json()["pending"] == []

    def test_skill_review_lifecycle(self, client):
        test_client, _, governance_selector = client
        alert = GovernanceAlert(
            alert_id="gov-0002",
            category=GovernanceCategory.SKILL_CAP,
            level=GovernanceLevel.WARNING,
            agent_id="heavy",
            message="5 skills > 4",
            suggestion="Split",
        )
        split = SkillSplitSuggestion(
            agent_id="heavy",
            current_skills=["a", "b", "c", "d", "e"],
            keep_skills=["a", "b", "c", "d"],
            overflow_skills=["e"],
            suggested_subagent_id="heavy_overflow",
        )
        governance_selector.prepare_skill_review("req-2", alert, split)

        # Check pending
        resp = test_client.get("/governance/skill-reviews")
        pending = resp.json()["pending"]
        assert len(pending) == 1
        assert pending[0]["split"]["suggested_subagent_id"] == "heavy_overflow"

        # Resolve with override
        resp = test_client.post(
            "/governance/skill-reviews/resolve",
            json={"request_id": "req-2", "accepted": False, "override": True},
        )
        assert resp.status_code == 200
        assert resp.json()["overridden"] is True

    def test_resolve_nonexistent_skill_review(self, client):
        test_client, _, _ = client
        resp = test_client.post(
            "/governance/skill-reviews/resolve",
            json={"request_id": "nope", "accepted": True},
        )
        assert resp.status_code == 404

    def test_governance_status_when_not_configured(self):
        """When no governance is attached."""
        from fastapi.testclient import TestClient

        from src.server import create_app

        orchestrator = MagicMock()
        orchestrator._governance = None
        _attach_governance_methods(orchestrator, None)
        orchestrator.get_status.return_value = {
            "session_id": "test",
            "registered_agents": [],
            "message_count": 0,
            "active_workflow": None,
            "provider": "azure_openai",
            "workiq_enrichment_available": False,
            "governance_enabled": False,
        }
        mcp_server = MagicMock()
        mcp_server.get_status.return_value = {"tools_count": 0}
        catalog = MagicMock()
        catalog.list_agents.return_value = []
        catalog.get_status.return_value = {"installed_skills": 0}
        workflow_engine = MagicMock()
        workflow_engine.list_workflows.return_value = []

        app = create_app(
            orchestrator=orchestrator,
            mcp_server=mcp_server,
            catalog=catalog,
            workflow_engine=workflow_engine,
            require_control_plane_api_key=False,
        )
        tc = TestClient(app)
        resp = tc.get("/governance/status")
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False


class TestChatStatusTaskContextIsolation:
    """Ensure /chat/status reads task-local phase state, not global orchestrator context."""

    def test_chat_status_uses_task_scoped_context(self):
        from fastapi.testclient import TestClient

        import src.server as server_module
        from src.server import create_app

        orchestrator = MagicMock()
        orchestrator._governance = None
        orchestrator.get_status.return_value = {
            "session_id": "test",
            "registered_agents": [],
            "message_count": 0,
            "active_workflow": None,
            "provider": "azure_openai",
            "workiq_enrichment_available": False,
            "governance_enabled": False,
        }

        orchestrator.context = MagicMock()
        orchestrator.context.get_memory.return_value = "global_phase_should_not_leak"

        mcp_server = MagicMock()
        mcp_server.get_status.return_value = {"tools_count": 0}
        catalog = MagicMock()
        catalog.list_agents.return_value = []
        catalog.get_status.return_value = {"installed_skills": 0}
        workflow_engine = MagicMock()
        workflow_engine.list_workflows.return_value = []

        app = create_app(
            orchestrator=orchestrator,
            mcp_server=mcp_server,
            catalog=catalog,
            workflow_engine=workflow_engine,
            require_control_plane_api_key=False,
        )
        client = TestClient(app)

        task_id = "test-task-context"
        task_ctx = ConversationContext()
        task_ctx.set_memory("pipeline_phase", "task_scoped_phase")
        server_module._chat_tasks[task_id] = {
            "status": "processing",
            "created_at": time.time(),
            "message": "hello",
            "response": None,
            "session_id": task_ctx.session_id,
            "error": None,
            "context": task_ctx,
        }

        try:
            status_resp = client.get(f"/chat/status/{task_id}")
            assert status_resp.status_code == 200
            payload = status_resp.json()
            assert payload["status"] == "processing"
            assert payload["pipeline_phase"] == "task_scoped_phase"
        finally:
            server_module._chat_tasks.pop(task_id, None)

    def test_chat_status_filters_pending_reviews_to_task_request_ids(self):
        from fastapi.testclient import TestClient

        import src.server as server_module
        from src.server import create_app

        orchestrator = MagicMock()
        orchestrator._governance = None
        orchestrator.get_status.return_value = {
            "session_id": "test",
            "registered_agents": [],
            "message_count": 0,
            "active_workflow": None,
            "provider": "azure_openai",
            "workiq_enrichment_available": False,
            "governance_enabled": False,
        }

        mcp_server = MagicMock()
        mcp_server.get_status.return_value = {"tools_count": 0}
        catalog = MagicMock()
        catalog.list_agents.return_value = []
        catalog.get_status.return_value = {"installed_skills": 0}
        workflow_engine = MagicMock()
        workflow_engine.list_workflows.return_value = []

        plan_selector = MagicMock()
        plan_selector.pending_plan_reviews.return_value = [
            {"request_id": "plan-owned", "resolved": False},
            {"request_id": "plan-other", "resolved": False},
        ]
        plan_selector.pending_resource_reviews.return_value = [
            {"request_id": "sub-owned", "resolved": False},
            {"request_id": "sub-other", "resolved": False},
        ]

        governance_selector = MagicMock()
        governance_selector.pending_context_reviews.return_value = [
            {"request_id": "gov-owned", "resolved": False},
            {"request_id": "gov-other", "resolved": False},
        ]
        governance_selector.pending_skill_reviews.return_value = [
            {"request_id": "skill-other", "resolved": False}
        ]
        governance_selector.pending_lifecycle_reviews.return_value = [
            {"request_id": "lifecycle-other", "resolved": False}
        ]

        app = create_app(
            orchestrator=orchestrator,
            mcp_server=mcp_server,
            catalog=catalog,
            workflow_engine=workflow_engine,
            plan_selector=plan_selector,
            governance_selector=governance_selector,
            require_control_plane_api_key=False,
        )
        client = TestClient(app)

        task_id = "test-task-pending-scope"
        task_ctx = ConversationContext()
        task_ctx.set_memory("plan_review_pending", "plan-owned")
        task_ctx.set_memory("resource_review_pending", "sub-owned")
        task_ctx.set_memory("governance_review_pending", "gov-owned")
        server_module._chat_tasks[task_id] = {
            "status": "processing",
            "created_at": time.time(),
            "message": "hello",
            "response": None,
            "session_id": task_ctx.session_id,
            "error": None,
            "context": task_ctx,
        }

        try:
            status_resp = client.get(f"/chat/status/{task_id}")
            assert status_resp.status_code == 200
            payload = status_resp.json()
            pending = payload["pending_reviews"]
            request_ids = {item["request_id"] for item in pending}
            assert request_ids == {"plan-owned", "sub-owned", "gov-owned"}
            assert {"plan", "sub_plan", "governance_context"} == {item["type"] for item in pending}
        finally:
            server_module._chat_tasks.pop(task_id, None)


class TestControlPlaneApiKeyAuth:
    """Test optional control-plane API key protection for sensitive routes."""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient

        from src.server import create_app

        orchestrator = MagicMock()
        guardian = GovernanceGuardian(config=_make_config())
        orchestrator._governance = guardian
        _attach_governance_methods(orchestrator, guardian)
        orchestrator.get_status.return_value = {
            "session_id": "test",
            "registered_agents": [],
            "message_count": 0,
            "active_workflow": None,
            "provider": "azure_openai",
            "workiq_enrichment_available": False,
            "governance_enabled": True,
            "governance": guardian.governance_report(),
        }

        mcp_server = MagicMock()
        mcp_server.get_status.return_value = {"tools_count": 0}
        mcp_response = MagicMock()
        mcp_response.to_dict.return_value = {"jsonrpc": "2.0", "result": {}, "id": 1}
        mcp_server.handle_request = AsyncMock(return_value=mcp_response)
        catalog = MagicMock()
        catalog.list_agents.return_value = []
        catalog.get_status.return_value = {"installed_skills": 0}
        workflow_engine = MagicMock()
        workflow_engine.list_workflows.return_value = []

        app = create_app(
            orchestrator=orchestrator,
            mcp_server=mcp_server,
            catalog=catalog,
            workflow_engine=workflow_engine,
            require_control_plane_api_key=True,
            control_plane_api_key="test-key",
            cors_allowed_origins=["http://localhost"],
            cors_allow_credentials=True,
        )
        return TestClient(app)

    def test_protected_endpoint_requires_api_key(self, client):
        resp = client.get("/governance/status")
        assert resp.status_code == 401

    def test_protected_endpoint_accepts_valid_api_key(self, client):
        resp = client.get("/governance/status", headers={"X-API-Key": "test-key"})
        assert resp.status_code == 200

    def test_chat_requires_api_key(self, client) -> None:
        resp = client.post("/chat", json={"message": "hello"})
        assert resp.status_code == 401

    def test_chat_accepts_valid_api_key(self, client) -> None:
        resp = client.post("/chat", headers={"X-API-Key": "test-key"}, json={"message": "hello"})
        assert resp.status_code == 200
        assert "task_id" in resp.json()

    def test_chat_status_requires_api_key(self, client) -> None:
        resp = client.get("/chat/status/test-task")
        assert resp.status_code == 401

    def test_chat_status_accepts_valid_api_key(self, client) -> None:
        resp = client.get("/chat/status/test-task", headers={"X-API-Key": "test-key"})
        assert resp.status_code == 404

    @pytest.mark.parametrize("path", ["/agents", "/skills", "/workflows"])
    def test_catalog_endpoints_require_api_key(self, client, path: str) -> None:
        resp = client.get(path)
        assert resp.status_code == 401

    @pytest.mark.parametrize("path", ["/agents", "/skills", "/workflows"])
    def test_catalog_endpoints_accept_valid_api_key(self, client, path: str) -> None:
        resp = client.get(path, headers={"X-API-Key": "test-key"})
        assert resp.status_code == 200

    def test_unprotected_endpoint_remains_accessible(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_chat_enriched_requires_api_key(self, client) -> None:
        resp = client.post("/chat/enriched", json={"message": "hello"})
        assert resp.status_code == 401

    def test_chat_enriched_accepts_valid_api_key(self, client) -> None:
        resp = client.post(
            "/chat/enriched",
            headers={"X-API-Key": "test-key"},
            json={"message": "hello"},
        )
        assert resp.status_code == 200

    def test_mcp_endpoint_requires_api_key(self, client) -> None:
        resp = client.post("/mcp", json={"method": "tools/list", "params": {}, "id": 1})
        assert resp.status_code == 401

    def test_mcp_endpoint_accepts_valid_api_key(self, client) -> None:
        resp = client.post(
            "/mcp",
            headers={"X-API-Key": "test-key"},
            json={"method": "tools/list", "params": {}, "id": 1},
        )
        assert resp.status_code == 200

    @pytest.mark.parametrize("path", ["/workiq/pending", "/workiq/routing-hints", "/plan/pending", "/sub-plan/pending"])
    def test_workiq_review_endpoints_accept_valid_api_key(self, client, path: str) -> None:
        resp = client.get(path, headers={"X-API-Key": "test-key"})
        assert resp.status_code == 200

    @pytest.mark.parametrize(
        ("path", "payload"),
        [
            ("/workiq/query", {"question": "status update"}),
            ("/workiq/pending", None),
            ("/workiq/routing-hints", None),
            ("/plan/pending", None),
            ("/sub-plan/pending", None),
            ("/workiq/select", {"request_id": "req-1", "selected_indices": [0]}),
            ("/workiq/accept-hints", {"request_id": "req-1", "accepted_indices": [0]}),
            ("/plan/accept", {"request_id": "req-1", "accepted_indices": [0]}),
            ("/sub-plan/accept", {"request_id": "req-1", "accepted_indices": [0]}),
        ],
    )
    def test_hitl_review_endpoints_require_api_key(
        self, client, path: str, payload: dict[str, Any] | None
    ) -> None:
        resp = client.get(path) if payload is None else client.post(path, json=payload)
        assert resp.status_code == 401

    def test_workiq_query_accepts_valid_api_key(self, client) -> None:
        resp = client.post("/workiq/query", headers={"X-API-Key": "test-key"}, json={"question": "status update"})
        assert resp.status_code == 501

    def test_misconfigured_api_key_guard_returns_503(self):
        from fastapi.testclient import TestClient

        from src.server import create_app

        orchestrator = MagicMock()
        guardian = GovernanceGuardian(config=_make_config())
        orchestrator._governance = guardian
        _attach_governance_methods(orchestrator, guardian)
        orchestrator.get_status.return_value = {
            "session_id": "test",
            "registered_agents": [],
            "message_count": 0,
            "active_workflow": None,
            "provider": "azure_openai",
            "workiq_enrichment_available": False,
            "governance_enabled": True,
            "governance": guardian.governance_report(),
        }

        mcp_server = MagicMock()
        mcp_server.get_status.return_value = {"tools_count": 0}
        catalog = MagicMock()
        catalog.list_agents.return_value = []
        catalog.get_status.return_value = {"installed_skills": 0}
        workflow_engine = MagicMock()
        workflow_engine.list_workflows.return_value = []

        app = create_app(
            orchestrator=orchestrator,
            mcp_server=mcp_server,
            catalog=catalog,
            workflow_engine=workflow_engine,
            require_control_plane_api_key=True,
            control_plane_api_key=None,
        )
        tc = TestClient(app)

        resp = tc.get("/governance/status", headers={"X-API-Key": "any"})
        assert resp.status_code == 503


class TestWorkIQQueryEndpointContract:
    """Ensure /workiq/query uses explicit WorkIQ query semantics."""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient

        from src.server import create_app
        from src.workiq.client import WorkIQResult
        from src.workiq.selector import WorkIQSelector

        orchestrator = MagicMock()
        orchestrator._governance = None
        orchestrator.get_status.return_value = {
            "session_id": "test",
            "registered_agents": [],
            "message_count": 0,
            "active_workflow": None,
            "provider": "azure_openai",
            "workiq_enrichment_available": True,
            "governance_enabled": False,
        }

        selector = WorkIQSelector(timeout=1.0)

        async def _mock_query_workiq(question: str):
            result = WorkIQResult(
                query=question,
                content="Section A\n\nSection B",
                sections=["Section A", "Section B"],
            )
            selector.prepare(result, "workiq-req-1")
            return result, "workiq-req-1"

        orchestrator.query_workiq = AsyncMock(side_effect=_mock_query_workiq)

        mcp_server = MagicMock()
        mcp_server.get_status.return_value = {"tools_count": 0}
        catalog = MagicMock()
        catalog.list_agents.return_value = []
        catalog.get_status.return_value = {"installed_skills": 0}
        workflow_engine = MagicMock()
        workflow_engine.list_workflows.return_value = []

        app = create_app(
            orchestrator=orchestrator,
            mcp_server=mcp_server,
            catalog=catalog,
            workflow_engine=workflow_engine,
            workiq_selector=selector,
            require_control_plane_api_key=False,
        )
        return TestClient(app), orchestrator, selector

    def test_workiq_query_returns_pending_selection_contract(self, client):
        tc, orchestrator, _selector = client
        resp = tc.post("/workiq/query", json={"question": "latest standup notes"})
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["request_id"] == "workiq-req-1"
        assert len(payload["sections"]) == 2
        assert payload["pending_selections"][0]["request_id"] == "workiq-req-1"
        orchestrator.query_workiq.assert_called_once_with("latest standup notes")

    def test_workiq_query_returns_502_on_workiq_error(self, client):
        from src.workiq.client import WorkIQResult

        tc, orchestrator, _selector = client
        orchestrator.query_workiq = AsyncMock(
            return_value=(WorkIQResult(query="latest standup notes", error="workiq unavailable"), None)
        )
        resp = tc.post("/workiq/query", json={"question": "latest standup notes"})
        assert resp.status_code == 502

    def test_workiq_query_does_not_leak_other_pending_requests(self, client):
        from src.workiq.client import WorkIQResult

        tc, _orchestrator, selector = client
        selector.prepare(
            WorkIQResult(query="other request", content="Other A\n\nOther B", sections=["Other A", "Other B"]),
            "other-req",
        )

        resp = tc.post("/workiq/query", json={"question": "latest standup notes"})
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["request_id"] == "workiq-req-1"
        assert [r["request_id"] for r in payload["pending_selections"]] == ["workiq-req-1"]


class TestGovernanceRoutesUsePublicOrchestratorMethods:
    """Ensure governance routes can operate via orchestrator public methods."""

    def test_governance_routes_work_without_private_governance_attr(self):
        from fastapi.testclient import TestClient

        from src.server import create_app

        class _Alert:
            def __init__(self) -> None:
                self.alert_id = "gov-1"
                self.category = "context_window"
                self.level = "warning"
                self.agent_id = "plan"
                self.message = "warning"
                self.suggestion = "decompose"
                self.details = {}

        class PublicGovernanceOrchestrator:
            def __init__(self) -> None:
                self._resolved: list[tuple[str, str]] = []

            def get_status(self) -> dict[str, Any]:
                return {
                    "session_id": "test",
                    "registered_agents": [],
                    "message_count": 0,
                    "active_workflow": None,
                    "provider": "azure_openai",
                    "workiq_enrichment_available": False,
                    "governance_enabled": True,
                }

            def get_governance_report(self) -> dict[str, Any]:
                return {
                    "cumulative_tokens": 10,
                    "hard_cap": 128000,
                    "warning_threshold": 110000,
                    "utilisation_pct": 0.0,
                    "agent_usage": {},
                    "max_skills_per_agent": 4,
                    "total_alerts": 1,
                    "unresolved_alerts": 1,
                    "skill_violations": [],
                }

            def get_unresolved_governance_alerts(self) -> list[Any]:
                return [_Alert()]

            def resolve_governance_alert(self, alert_id: str, resolution: str = "accepted") -> bool:
                self._resolved.append((alert_id, resolution))
                return alert_id == "gov-1"

        orchestrator = PublicGovernanceOrchestrator()
        mcp_server = MagicMock()
        mcp_server.get_status.return_value = {"tools_count": 0}
        catalog = MagicMock()
        catalog.list_agents.return_value = []
        catalog.get_status.return_value = {"installed_skills": 0}
        workflow_engine = MagicMock()
        workflow_engine.list_workflows.return_value = []

        app = create_app(
            orchestrator=orchestrator,
            mcp_server=mcp_server,
            catalog=catalog,
            workflow_engine=workflow_engine,
            require_control_plane_api_key=False,
        )
        tc = TestClient(app)

        status = tc.get("/governance/status")
        assert status.status_code == 200
        assert status.json()["enabled"] is True

        alerts = tc.get("/governance/alerts")
        assert alerts.status_code == 200
        assert alerts.json()["alerts"][0]["alert_id"] == "gov-1"

        resolved = tc.post("/governance/resolve-alert", json={"alert_id": "gov-1", "resolution": "accepted"})
        assert resolved.status_code == 200
        assert orchestrator._resolved == [("gov-1", "accepted")]

    def test_governance_routes_require_public_contract_without_mutating_orchestrator(self):
        from fastapi.testclient import TestClient

        from src.server import create_app

        class LegacyGovernanceOrchestrator:
            def __init__(self) -> None:
                self._governance = GovernanceGuardian(config=_make_config())

            def get_status(self) -> dict[str, Any]:
                return {
                    "session_id": "test",
                    "registered_agents": [],
                    "message_count": 0,
                    "active_workflow": None,
                    "provider": "azure_openai",
                    "workiq_enrichment_available": False,
                    "governance_enabled": True,
                }

        orchestrator = LegacyGovernanceOrchestrator()
        assert not hasattr(orchestrator, "get_governance_report")

        mcp_server = MagicMock()
        mcp_server.get_status.return_value = {"tools_count": 0}
        catalog = MagicMock()
        catalog.list_agents.return_value = []
        catalog.get_status.return_value = {"installed_skills": 0}
        workflow_engine = MagicMock()
        workflow_engine.list_workflows.return_value = []

        app = create_app(
            orchestrator=orchestrator,
            mcp_server=mcp_server,
            catalog=catalog,
            workflow_engine=workflow_engine,
            require_control_plane_api_key=False,
        )
        tc = TestClient(app)

        status = tc.get("/governance/status")
        assert status.status_code == 200
        assert status.json()["enabled"] is False
        assert not hasattr(orchestrator, "get_governance_report")


class TestGitHubEndpointsUseGovernedDispatch:
    """Ensure GitHub routes go through plan-first orchestrator processing."""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient

        from src.orchestrator.context import AgentResult, ConversationContext
        from src.server import create_app

        orchestrator = MagicMock()
        guardian = GovernanceGuardian(config=_make_config())
        orchestrator._governance = guardian
        _attach_governance_methods(orchestrator, guardian)
        orchestrator.get_status.return_value = {
            "session_id": "test",
            "registered_agents": ["github_tracker"],
            "message_count": 0,
            "active_workflow": None,
            "provider": "azure_openai",
            "workiq_enrichment_available": False,
            "governance_enabled": True,
        }

        async def _mock_process_with_routing(message, routing, *, ctx=None, auto_accept_hitl=False):
            run_ctx = ctx or ConversationContext()
            run_ctx.add_result(AgentResult(agent_id="plan", content="Plan ran first"))
            run_ctx.add_result(
                AgentResult(
                    agent_id="github_tracker",
                    content=f"github_tracker:{message}",
                    artifacts={"action": routing.extracted_params.get("action")},
                )
            )
            assert auto_accept_hitl is True
            return "aggregated", run_ctx

        orchestrator.process_with_routing = AsyncMock(side_effect=_mock_process_with_routing)
        orchestrator._dispatch = AsyncMock()

        mcp_server = MagicMock()
        mcp_server.get_status.return_value = {"tools_count": 0}
        catalog = MagicMock()
        catalog.list_agents.return_value = []
        catalog.get_status.return_value = {"installed_skills": 0}
        workflow_engine = MagicMock()
        workflow_engine.list_workflows.return_value = []

        app = create_app(
            orchestrator=orchestrator,
            mcp_server=mcp_server,
            catalog=catalog,
            workflow_engine=workflow_engine,
            require_control_plane_api_key=False,
        )
        return TestClient(app), orchestrator

    def test_document_commit_uses_plan_first_pipeline(self, client):
        tc, orchestrator = client
        resp = tc.post("/github/document-commit", json={"commit_message": "feat: add policy", "repo": "o/r"})
        assert resp.status_code == 200
        assert resp.json()["artifacts"]["action"] == "document_commit"
        orchestrator.process_with_routing.assert_called_once()
        orchestrator._dispatch.assert_not_called()
        args = orchestrator.process_with_routing.call_args
        assert args.args[1].primary_agent == "github_tracker"
        assert args.args[1].extracted_params["action"] == "document_commit"
        assert args.kwargs["auto_accept_hitl"] is True

    def test_manage_issue_uses_plan_first_pipeline(self, client):
        tc, orchestrator = client
        resp = tc.post("/github/manage-issue", json={"action": "create", "title": "Bug", "repo": "o/r"})
        assert resp.status_code == 200
        assert resp.json()["artifacts"]["action"] == "manage_issue"
        orchestrator._dispatch.assert_not_called()
        args = orchestrator.process_with_routing.call_args
        assert args.args[1].extracted_params["action"] == "manage_issue"
        assert args.kwargs["auto_accept_hitl"] is True

    def test_changelog_uses_plan_first_pipeline(self, client):
        tc, orchestrator = client
        resp = tc.post("/github/changelog", json={"repo": "o/r", "from_ref": "v0.1.0", "to_ref": "v0.1.1"})
        assert resp.status_code == 200
        assert resp.json()["artifacts"]["action"] == "changelog"
        orchestrator._dispatch.assert_not_called()
        args = orchestrator.process_with_routing.call_args
        assert args.args[1].extracted_params["action"] == "changelog"
        assert args.kwargs["auto_accept_hitl"] is True


class TestLifecycleServerEndpoints:
    """Test agent lifecycle REST API endpoints via TestClient."""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient

        from src.server import create_app

        orchestrator = MagicMock()
        guardian = GovernanceGuardian(config=_make_config())
        orchestrator._governance = guardian
        _attach_governance_methods(orchestrator, guardian)
        orchestrator.get_status.return_value = {
            "session_id": "test",
            "registered_agents": ["a", "b"],
            "enabled_agents": ["a", "b"],
            "disabled_agents": [],
            "message_count": 0,
            "active_workflow": None,
            "provider": "azure_openai",
            "workiq_enrichment_available": False,
            "governance_enabled": True,
        }
        orchestrator.list_enabled_agents.return_value = ["a", "b"]
        orchestrator.list_disabled_agents.return_value = []

        # disable_agent is async
        async def _mock_disable(agent_id):
            return {"agent_id": agent_id, "status": "disabled"}

        orchestrator.disable_agent = AsyncMock(side_effect=_mock_disable)

        async def _mock_enable(agent_id):
            return {"agent_id": agent_id, "status": "enabled"}

        orchestrator.enable_agent = AsyncMock(side_effect=_mock_enable)

        async def _mock_unregister(agent_id):
            return {"agent_id": agent_id, "status": "removed"}

        orchestrator.unregister_agent = AsyncMock(side_effect=_mock_unregister)

        mcp_server = MagicMock()
        mcp_server.get_status.return_value = {"tools_count": 0}
        catalog = MagicMock()
        catalog.list_agents.return_value = []
        catalog.get_status.return_value = {"installed_skills": 0}
        workflow_engine = MagicMock()
        workflow_engine.list_workflows.return_value = []

        governance_selector = GovernanceSelector(timeout=1.0)

        app = create_app(
            orchestrator=orchestrator,
            mcp_server=mcp_server,
            catalog=catalog,
            workflow_engine=workflow_engine,
            governance_selector=governance_selector,
            require_control_plane_api_key=False,
        )
        return TestClient(app), orchestrator, governance_selector

    def test_lifecycle_reviews_empty(self, client):
        tc, _, _ = client
        resp = tc.get("/governance/lifecycle-reviews")
        assert resp.status_code == 200
        assert resp.json()["pending"] == []

    def test_lifecycle_review_lifecycle(self, client):
        tc, _, selector = client
        selector.prepare_lifecycle_review(
            "lc-1",
            "disable",
            "log_analysis",
            ["plan", "code_research"],
        )
        resp = tc.get("/governance/lifecycle-reviews")
        pending = resp.json()["pending"]
        assert len(pending) == 1
        assert pending[0]["action"] == "disable"

        # Resolve
        resp = tc.post(
            "/governance/lifecycle-reviews/resolve",
            json={"request_id": "lc-1", "accepted": True, "user_note": "OK"},
        )
        assert resp.status_code == 200
        assert resp.json()["accepted"] is True

    def test_resolve_nonexistent_lifecycle_review(self, client):
        tc, _, _ = client
        resp = tc.post(
            "/governance/lifecycle-reviews/resolve",
            json={"request_id": "nope", "accepted": True},
        )
        assert resp.status_code == 404

    def test_disable_agent_endpoint(self, client):
        tc, orch, _ = client
        resp = tc.post("/agents/a/disable")
        assert resp.status_code == 200
        assert resp.json()["status"] == "disabled"
        orch.disable_agent.assert_called_once_with("a")

    def test_disable_nonexistent_agent(self, client):
        tc, orch, _ = client
        orch.disable_agent = AsyncMock(side_effect=KeyError("nope"))
        resp = tc.post("/agents/nope/disable")
        assert resp.status_code == 404

    def test_enable_agent_endpoint(self, client):
        tc, orch, _ = client
        resp = tc.post("/agents/a/enable")
        assert resp.status_code == 200
        orch.enable_agent.assert_called_once_with("a")

    def test_enable_nonexistent_agent(self, client):
        tc, orch, _ = client
        orch.enable_agent.side_effect = KeyError("nope")
        resp = tc.post("/agents/nope/enable")
        assert resp.status_code == 404

    def test_remove_agent_endpoint(self, client):
        tc, orch, _ = client
        resp = tc.delete("/agents/a")
        assert resp.status_code == 200
        assert resp.json()["status"] == "removed"
        orch.unregister_agent.assert_called_once_with("a")

    def test_remove_nonexistent_agent(self, client):
        tc, orch, _ = client
        orch.unregister_agent = AsyncMock(side_effect=KeyError("nope"))
        resp = tc.delete("/agents/nope")
        assert resp.status_code == 404

    def test_list_enabled_agents_endpoint(self, client):
        tc, _, _ = client
        resp = tc.get("/agents/enabled")
        assert resp.status_code == 200
        assert resp.json()["enabled_agents"] == ["a", "b"]

    def test_list_disabled_agents_endpoint(self, client):
        tc, _, _ = client
        resp = tc.get("/agents/disabled")
        assert resp.status_code == 200
        assert resp.json()["disabled_agents"] == []
