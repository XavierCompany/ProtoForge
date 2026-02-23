"""HITL selector for governance alerts — context window and skill cap reviews.

Follows the same prepare → expose → wait → resolve pattern established by
:class:`~src.orchestrator.plan_selector.PlanSelector` and
:class:`~src.workiq.selector.WorkIQSelector`.

Two review types:

1. **Context Window Review** — triggered when cumulative tokens cross the
   warning threshold.  The human can accept task decomposition (spawn a
   sub-agent) or reject it (continue at risk).

2. **Skill Cap Review** — triggered at manifest load time when an agent
   declares > 4 skills.  The human can accept the suggested split, customise
   which skills stay vs. move, or override the violation.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from src.governance.guardian import (
        ContextDecompositionSuggestion,
        GovernanceAlert,
        SkillSplitSuggestion,
    )

logger = structlog.get_logger(__name__)


# ── Data-classes ────────────────────────────────────────────────────────────


@dataclass
class ContextWindowReview:
    """Pending HITL request for a context window threshold breach."""

    request_id: str
    alert: GovernanceAlert
    decomposition: ContextDecompositionSuggestion | None = None
    accepted: bool = False
    resolved: bool = False
    user_note: str = ""


@dataclass
class SkillCapReview:
    """Pending HITL request for a skill cap violation."""

    request_id: str
    alert: GovernanceAlert
    split_suggestion: SkillSplitSuggestion | None = None
    accepted: bool = False
    resolved: bool = False
    custom_keep: list[str] = field(default_factory=list)
    custom_overflow: list[str] = field(default_factory=list)
    overridden: bool = False


# ── GovernanceSelector ──────────────────────────────────────────────────────


class GovernanceSelector:
    """HITL gate for governance alerts.

    Usage:
    - ``prepare_context_review`` / ``resolve_context_review`` / ``wait_for_context_review``
    - ``prepare_skill_review`` / ``resolve_skill_review`` / ``wait_for_skill_review``

    If no human responds within *timeout* seconds the request auto-resolves
    (fail-open: accepts the suggestion by default).
    """

    def __init__(self, timeout: float = 120.0) -> None:
        self._timeout = timeout

        # Context window reviews
        self._context_pending: dict[str, ContextWindowReview] = {}
        self._context_events: dict[str, asyncio.Event] = {}

        # Skill cap reviews
        self._skill_pending: dict[str, SkillCapReview] = {}
        self._skill_events: dict[str, asyncio.Event] = {}

    # ── Context Window Reviews ──────────────────────────────────────────

    def prepare_context_review(
        self,
        request_id: str,
        alert: GovernanceAlert,
        decomposition: ContextDecompositionSuggestion | None = None,
    ) -> ContextWindowReview:
        """Create a HITL review for a context window threshold breach."""
        review = ContextWindowReview(
            request_id=request_id,
            alert=alert,
            decomposition=decomposition,
        )
        self._context_pending[request_id] = review
        self._context_events[request_id] = asyncio.Event()

        logger.info(
            "context_review_prepared",
            request_id=request_id,
            level=alert.level,
            agent_id=alert.agent_id,
        )
        return review

    def resolve_context_review(
        self,
        request_id: str,
        accepted: bool,
        user_note: str = "",
    ) -> bool:
        """Human resolves a context window review.

        - ``accepted=True``  → decompose the task (spawn sub-agent)
        - ``accepted=False`` → continue execution at the operator's risk
        """
        review = self._context_pending.get(request_id)
        if not review:
            logger.warning("context_review_not_found", request_id=request_id)
            return False

        review.accepted = accepted
        review.resolved = True
        review.user_note = user_note

        event = self._context_events.get(request_id)
        if event:
            event.set()

        logger.info(
            "context_review_resolved",
            request_id=request_id,
            accepted=accepted,
        )
        return True

    async def wait_for_context_review(self, request_id: str) -> ContextWindowReview:
        """Block until the human resolves the context review (or timeout)."""
        review = self._context_pending.get(request_id)
        if not review:
            raise KeyError(f"No pending context review for {request_id}")
        if review.resolved:
            return review

        event = self._context_events[request_id]
        try:
            await asyncio.wait_for(event.wait(), timeout=self._timeout)
        except TimeoutError:
            logger.warning("context_review_timeout", request_id=request_id)
            review.accepted = True  # fail-open: accept decomposition
            review.resolved = True

        return review

    def pending_context_reviews(self) -> list[dict[str, Any]]:
        """Unresolved context window reviews (for REST API)."""
        return [
            {
                "request_id": r.request_id,
                "level": r.alert.level,
                "agent_id": r.alert.agent_id,
                "message": r.alert.message,
                "suggestion": r.alert.suggestion,
                "decomposition": (
                    {
                        "current_tokens": r.decomposition.current_tokens,
                        "hard_cap": r.decomposition.hard_cap,
                        "agent_usage": r.decomposition.agent_usage,
                        "recommendation": r.decomposition.suggestion,
                    }
                    if r.decomposition
                    else None
                ),
                "resolved": r.resolved,
            }
            for r in self._context_pending.values()
            if not r.resolved
        ]

    def cleanup_context_review(self, request_id: str) -> None:
        self._context_pending.pop(request_id, None)
        self._context_events.pop(request_id, None)

    # ── Skill Cap Reviews ───────────────────────────────────────────────

    def prepare_skill_review(
        self,
        request_id: str,
        alert: GovernanceAlert,
        split_suggestion: SkillSplitSuggestion | None = None,
    ) -> SkillCapReview:
        """Create a HITL review for a skill cap violation."""
        review = SkillCapReview(
            request_id=request_id,
            alert=alert,
            split_suggestion=split_suggestion,
        )
        self._skill_pending[request_id] = review
        self._skill_events[request_id] = asyncio.Event()

        logger.info(
            "skill_review_prepared",
            request_id=request_id,
            agent_id=alert.agent_id,
        )
        return review

    def resolve_skill_review(
        self,
        request_id: str,
        accepted: bool,
        *,
        custom_keep: list[str] | None = None,
        custom_overflow: list[str] | None = None,
        override: bool = False,
    ) -> bool:
        """Human resolves a skill cap review.

        - ``accepted=True``   → apply the suggested (or custom) split
        - ``accepted=False``  → do not split
        - ``override=True``   → acknowledge violation, keep all skills
        """
        review = self._skill_pending.get(request_id)
        if not review:
            logger.warning("skill_review_not_found", request_id=request_id)
            return False

        review.accepted = accepted
        review.overridden = override
        review.resolved = True
        if custom_keep is not None:
            review.custom_keep = custom_keep
        if custom_overflow is not None:
            review.custom_overflow = custom_overflow

        event = self._skill_events.get(request_id)
        if event:
            event.set()

        logger.info(
            "skill_review_resolved",
            request_id=request_id,
            accepted=accepted,
            overridden=override,
        )
        return True

    async def wait_for_skill_review(self, request_id: str) -> SkillCapReview:
        """Block until the human resolves the skill review (or timeout)."""
        review = self._skill_pending.get(request_id)
        if not review:
            raise KeyError(f"No pending skill review for {request_id}")
        if review.resolved:
            return review

        event = self._skill_events[request_id]
        try:
            await asyncio.wait_for(event.wait(), timeout=self._timeout)
        except TimeoutError:
            logger.warning("skill_review_timeout", request_id=request_id)
            review.accepted = True  # fail-open: accept the split
            review.resolved = True

        return review

    def pending_skill_reviews(self) -> list[dict[str, Any]]:
        """Unresolved skill cap reviews (for REST API)."""
        results: list[dict[str, Any]] = []
        for r in self._skill_pending.values():
            if r.resolved:
                continue
            entry: dict[str, Any] = {
                "request_id": r.request_id,
                "agent_id": r.alert.agent_id,
                "message": r.alert.message,
                "suggestion": r.alert.suggestion,
                "resolved": r.resolved,
            }
            if r.split_suggestion:
                entry["split"] = {
                    "current_skills": r.split_suggestion.current_skills,
                    "keep_skills": r.split_suggestion.keep_skills,
                    "overflow_skills": r.split_suggestion.overflow_skills,
                    "suggested_subagent_id": r.split_suggestion.suggested_subagent_id,
                }
            results.append(entry)
        return results

    def get_skill_review(self, request_id: str) -> SkillCapReview | None:
        return self._skill_pending.get(request_id)

    def get_context_review(self, request_id: str) -> ContextWindowReview | None:
        return self._context_pending.get(request_id)

    def cleanup_skill_review(self, request_id: str) -> None:
        self._skill_pending.pop(request_id, None)
        self._skill_events.pop(request_id, None)
