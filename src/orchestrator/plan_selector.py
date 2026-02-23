"""Human-in-the-loop selector for Plan Agent and Sub-Plan Agent output.

Provides two HITL gates that sit between the Plan-first pipeline and
the task-agent fan-out:

**Plan HITL (Phase A):**
After the Plan Agent produces a strategy and recommended sub-agents,
the human reviews and accepts / rejects individual suggestions and
keywords before the Sub-Plan Agent runs.

**Sub-Plan HITL (Phase B):**
After the Sub-Plan Agent produces a resource deployment plan, the
human reviews each proposed resource and can supply an additional
brief (defaults to *"you should aim to create the minimum resources
needed to demonstrate the functionality as an example"*).

Both phases follow the same prepare → expose → wait → resolve pattern
used by :class:`~src.workiq.selector.WorkIQSelector`.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_DEFAULT_SUB_PLAN_BRIEF = (
    "You should aim to create the minimum resources needed "
    "to demonstrate the functionality as an example."
)


# ── dataclasses ─────────────────────────────────────────────────────────────


@dataclass
class PlanSuggestion:
    """One suggestion from the Plan Agent for human review."""

    index: int
    agent_id: str
    summary: str
    keywords: list[str] = field(default_factory=list)


@dataclass
class PlanReviewRequest:
    """Pending HITL request for Plan Agent suggestions."""

    request_id: str
    plan_summary: str
    suggestions: list[PlanSuggestion]
    accepted_indices: list[int] = field(default_factory=list)
    resolved: bool = False


@dataclass
class ResourceItem:
    """One proposed resource from the Sub-Plan Agent."""

    index: int
    name: str
    resource_type: str
    purpose: str
    effort: str
    dependencies: list[str] = field(default_factory=list)


@dataclass
class ResourceReviewRequest:
    """Pending HITL request for Sub-Plan Agent resource plan."""

    request_id: str
    resource_summary: str
    resources: list[ResourceItem]
    user_brief: str = _DEFAULT_SUB_PLAN_BRIEF
    accepted_indices: list[int] = field(default_factory=list)
    resolved: bool = False


# ── PlanSelector ────────────────────────────────────────────────────────────


class PlanSelector:
    """HITL gate for Plan Agent and Sub-Plan Agent output.

    Usage mirrors :class:`~src.workiq.selector.WorkIQSelector`:

    - ``prepare_plan_review`` / ``resolve_plan_review`` / ``wait_for_plan_review``
    - ``prepare_resource_review`` / ``resolve_resource_review`` / ``wait_for_resource_review``

    If no human responds within *timeout* seconds the request auto-
    resolves with *all* items accepted (fail-open).
    """

    def __init__(self, timeout: float = 120.0) -> None:
        self._timeout = timeout

        # Phase A – Plan Agent HITL
        self._plan_pending: dict[str, PlanReviewRequest] = {}
        self._plan_events: dict[str, asyncio.Event] = {}

        # Phase B – Sub-Plan Agent HITL
        self._resource_pending: dict[str, ResourceReviewRequest] = {}
        self._resource_events: dict[str, asyncio.Event] = {}

    # ── Phase A: Plan Agent suggestions ─────────────────────────────────

    def prepare_plan_review(
        self,
        request_id: str,
        plan_content: str,
        recommended_agents: list[str],
        *,
        plan_artifacts: dict[str, Any] | None = None,
    ) -> PlanReviewRequest:
        """Build a HITL request from the Plan Agent's output."""
        suggestions: list[PlanSuggestion] = []
        for idx, agent_id in enumerate(recommended_agents):
            keywords = [agent_id]
            # Pull step info from artifacts when available
            if plan_artifacts:
                steps = plan_artifacts.get("steps", [])
                if idx < len(steps):
                    keywords.append(str(steps[idx]))
            suggestions.append(PlanSuggestion(
                index=idx,
                agent_id=agent_id,
                summary=f"Invoke {agent_id} agent",
                keywords=keywords,
            ))

        req = PlanReviewRequest(
            request_id=request_id,
            plan_summary=plan_content[:500],
            suggestions=suggestions,
        )

        if len(suggestions) <= 1:
            req.accepted_indices = list(range(len(suggestions)))
            req.resolved = True
        else:
            self._plan_pending[request_id] = req
            self._plan_events[request_id] = asyncio.Event()

        logger.info(
            "plan_review_prepared",
            request_id=request_id,
            suggestion_count=len(suggestions),
            auto_resolved=req.resolved,
        )
        return req

    def resolve_plan_review(
        self, request_id: str, accepted_indices: list[int],
    ) -> bool:
        """Human accepts / rejects plan suggestions."""
        req = self._plan_pending.get(request_id)
        if not req:
            logger.warning("plan_review_not_found", request_id=request_id)
            return False

        valid = [i for i in accepted_indices if 0 <= i < len(req.suggestions)]
        req.accepted_indices = valid if valid else list(range(len(req.suggestions)))
        req.resolved = True

        event = self._plan_events.get(request_id)
        if event:
            event.set()

        logger.info(
            "plan_review_resolved",
            request_id=request_id,
            accepted=req.accepted_indices,
        )
        return True

    async def wait_for_plan_review(self, request_id: str) -> PlanReviewRequest:
        """Block until the human resolves the plan review (or timeout)."""
        req = self._plan_pending.get(request_id)
        if not req:
            raise KeyError(f"No pending plan review for {request_id}")
        if req.resolved:
            return req

        event = self._plan_events[request_id]
        try:
            await asyncio.wait_for(event.wait(), timeout=self._timeout)
        except TimeoutError:
            logger.warning("plan_review_timeout", request_id=request_id)
            req.accepted_indices = list(range(len(req.suggestions)))
            req.resolved = True

        return req

    def accepted_plan_agents(self, request_id: str) -> list[str]:
        """Return the agent IDs the human accepted."""
        req = self._plan_pending.get(request_id)
        if not req or not req.resolved:
            return []
        return [
            req.suggestions[i].agent_id
            for i in req.accepted_indices
            if 0 <= i < len(req.suggestions)
        ]

    def pending_plan_reviews(self) -> list[dict[str, Any]]:
        """Unresolved plan review requests (for REST API)."""
        return [
            {
                "request_id": req.request_id,
                "plan_summary": req.plan_summary,
                "suggestions": [
                    {
                        "index": s.index,
                        "agent_id": s.agent_id,
                        "summary": s.summary,
                        "keywords": s.keywords,
                    }
                    for s in req.suggestions
                ],
                "resolved": req.resolved,
            }
            for req in self._plan_pending.values()
            if not req.resolved
        ]

    def cleanup_plan_review(self, request_id: str) -> None:
        self._plan_pending.pop(request_id, None)
        self._plan_events.pop(request_id, None)

    # ── Phase B: Sub-Plan Agent resource review ─────────────────────────

    def prepare_resource_review(
        self,
        request_id: str,
        sub_plan_content: str,
        resources: list[dict[str, Any]],
        *,
        user_brief: str = "",
    ) -> ResourceReviewRequest:
        """Build a HITL request from the Sub-Plan Agent's resource plan."""
        items: list[ResourceItem] = []
        for idx, res in enumerate(resources):
            items.append(ResourceItem(
                index=idx,
                name=res.get("name", f"resource_{idx}"),
                resource_type=res.get("type", "unknown"),
                purpose=res.get("purpose", ""),
                effort=res.get("effort", "unknown"),
                dependencies=res.get("dependencies", []),
            ))

        req = ResourceReviewRequest(
            request_id=request_id,
            resource_summary=sub_plan_content[:500],
            resources=items,
            user_brief=user_brief or _DEFAULT_SUB_PLAN_BRIEF,
        )

        if len(items) == 0:
            req.accepted_indices = []
            req.resolved = True
        else:
            self._resource_pending[request_id] = req
            self._resource_events[request_id] = asyncio.Event()

        logger.info(
            "resource_review_prepared",
            request_id=request_id,
            resource_count=len(items),
            has_brief=bool(user_brief),
            auto_resolved=req.resolved,
        )
        return req

    def resolve_resource_review(
        self,
        request_id: str,
        accepted_indices: list[int],
        *,
        user_brief: str = "",
    ) -> bool:
        """Human accepts / rejects proposed resources and optionally sets a brief."""
        req = self._resource_pending.get(request_id)
        if not req:
            logger.warning("resource_review_not_found", request_id=request_id)
            return False

        valid = [i for i in accepted_indices if 0 <= i < len(req.resources)]
        req.accepted_indices = valid if valid else list(range(len(req.resources)))
        if user_brief:
            req.user_brief = user_brief
        req.resolved = True

        event = self._resource_events.get(request_id)
        if event:
            event.set()

        logger.info(
            "resource_review_resolved",
            request_id=request_id,
            accepted=req.accepted_indices,
            brief_overridden=bool(user_brief),
        )
        return True

    async def wait_for_resource_review(
        self, request_id: str,
    ) -> ResourceReviewRequest:
        """Block until the human resolves the resource review (or timeout)."""
        req = self._resource_pending.get(request_id)
        if not req:
            raise KeyError(f"No pending resource review for {request_id}")
        if req.resolved:
            return req

        event = self._resource_events[request_id]
        try:
            await asyncio.wait_for(event.wait(), timeout=self._timeout)
        except TimeoutError:
            logger.warning("resource_review_timeout", request_id=request_id)
            req.accepted_indices = list(range(len(req.resources)))
            req.resolved = True

        return req

    def accepted_resources(self, request_id: str) -> list[ResourceItem]:
        """Return the resource items the human accepted."""
        req = self._resource_pending.get(request_id)
        if not req or not req.resolved:
            return []
        return [
            req.resources[i]
            for i in req.accepted_indices
            if 0 <= i < len(req.resources)
        ]

    def resource_brief(self, request_id: str) -> str:
        """Return the user brief (default or overridden) for the resource plan."""
        req = self._resource_pending.get(request_id)
        if not req:
            return _DEFAULT_SUB_PLAN_BRIEF
        return req.user_brief

    def pending_resource_reviews(self) -> list[dict[str, Any]]:
        """Unresolved resource review requests (for REST API)."""
        return [
            {
                "request_id": req.request_id,
                "resource_summary": req.resource_summary,
                "user_brief": req.user_brief,
                "resources": [
                    {
                        "index": r.index,
                        "name": r.name,
                        "type": r.resource_type,
                        "purpose": r.purpose,
                        "effort": r.effort,
                        "dependencies": r.dependencies,
                    }
                    for r in req.resources
                ],
                "resolved": req.resolved,
            }
            for req in self._resource_pending.values()
            if not req.resolved
        ]

    def cleanup_resource_review(self, request_id: str) -> None:
        self._resource_pending.pop(request_id, None)
        self._resource_events.pop(request_id, None)
