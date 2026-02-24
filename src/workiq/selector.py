"""Human-in-the-loop selector for WorkIQ results.

When the orchestrator receives Work IQ output it should NOT blindly
inject all of it into the agent pipeline.  Instead, the selector
presents numbered options to the user (via REST API or CLI) and waits
for them to pick which sections are relevant.

**Phase 1 — Content selection:**
The user picks which WorkIQ sections to include.

**Phase 2 — Routing-keyword selection:**
The system extracts routing keywords from the selected content and
presents them for HITL review.  Accepted keywords feed into the
Intent Router as enrichment hints, steering which agents are chosen.

This keeps the human in control of *what* organisational context leaks
into the multi-agent pipeline, which is important for privacy and
relevance filtering.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from src.orchestrator.router import RoutingKeywordHint
    from src.workiq.client import WorkIQResult

logger = structlog.get_logger(__name__)


@dataclass
class SelectionOption:
    """A single selectable item presented to the user."""

    index: int
    preview: str
    full_content: str
    source: str = ""


@dataclass
class SelectionRequest:
    """Pending selection request waiting for user input."""

    request_id: str
    query: str
    options: list[SelectionOption]
    selected_indices: list[int] = field(default_factory=list)
    resolved: bool = False


@dataclass
class RoutingHintRequest:
    """Pending HITL request for routing-keyword selection.

    After the user picks which WorkIQ sections to keep (Phase 1), the
    system extracts routing keywords and presents them here for Phase 2
    review.
    """

    request_id: str
    hints: list[RoutingKeywordHint]
    accepted_indices: list[int] = field(default_factory=list)
    resolved: bool = False


class WorkIQSelector:
    """Present WorkIQ results to the user and wait for selection.

    The workflow is:

    1. ``prepare(result)`` → builds a :class:`SelectionRequest` with numbered
       options derived from the Work IQ ``sections``.
    2. The request is exposed via the ``/workiq/pending`` REST endpoint so the
       Inspector UI or CLI can display it.
    3. ``resolve(request_id, indices)`` records the user's choices and
       unblocks the waiting agent.
    4. ``selected_content(request_id)`` returns only the sections the user
       picked, ready to be injected as context.

    If no human responds within the timeout, *all* sections are used
    automatically (fail-open).
    """

    def __init__(self, timeout: float = 120.0) -> None:
        self._timeout = timeout
        # Phase 1: content section selection
        self._pending: dict[str, SelectionRequest] = {}
        self._events: dict[str, asyncio.Event] = {}
        # Phase 2: routing-keyword hint selection
        self._pending_hints: dict[str, RoutingHintRequest] = {}
        self._hint_events: dict[str, asyncio.Event] = {}

    # ── Public API ──────────────────────────────────────────────────────

    def prepare(self, result: WorkIQResult, request_id: str) -> SelectionRequest:
        """Build a selection request from a WorkIQ result.

        Each logical section of the Work IQ response becomes one selectable
        option.  If there is only one section the request is auto-resolved.
        """
        self.sweep_resolved()
        options: list[SelectionOption] = []
        for idx, section in enumerate(result.sections):
            preview = section[:120] + ("…" if len(section) > 120 else "")
            source = result.sources[idx] if idx < len(result.sources) else ""
            options.append(
                SelectionOption(
                    index=idx,
                    preview=preview,
                    full_content=section,
                    source=source,
                )
            )

        req = SelectionRequest(
            request_id=request_id,
            query=result.query,
            options=options,
        )

        # Auto-resolve when there's 0 or 1 option — no point asking.
        if len(options) <= 1:
            req.selected_indices = list(range(len(options)))
            req.resolved = True
        else:
            self._pending[request_id] = req
            self._events[request_id] = asyncio.Event()

        logger.info(
            "workiq_selection_prepared",
            request_id=request_id,
            options=len(options),
            auto_resolved=req.resolved,
        )
        return req

    def resolve(self, request_id: str, indices: list[int]) -> bool:
        """User has made their selection — record it and unblock the waiter."""
        req = self._pending.get(request_id)
        if not req:
            logger.warning("workiq_selection_not_found", request_id=request_id)
            return False
        if req.resolved:
            logger.info("workiq_selection_already_resolved", request_id=request_id)
            return False

        valid = [i for i in indices if 0 <= i < len(req.options)]
        req.selected_indices = valid if valid else list(range(len(req.options)))
        req.resolved = True

        event = self._events.get(request_id)
        if event:
            event.set()

        logger.info(
            "workiq_selection_resolved",
            request_id=request_id,
            selected=req.selected_indices,
        )
        return True

    async def wait_for_selection(self, request_id: str) -> SelectionRequest:
        """Wait until the user resolves the selection (or timeout → use all)."""
        req = self._pending.get(request_id)
        if not req:
            raise KeyError(f"No pending selection for {request_id}")

        if req.resolved:
            return req

        event = self._events[request_id]
        try:
            await asyncio.wait_for(event.wait(), timeout=self._timeout)
        except TimeoutError:
            logger.warning("workiq_selection_timeout", request_id=request_id)
            req.selected_indices = list(range(len(req.options)))
            req.resolved = True

        return req

    def selected_content(self, request_id: str) -> str:
        """Return the concatenated content of the selected sections."""
        # Check both _pending and auto-resolved
        req = self._pending.get(request_id)
        if not req:
            return ""
        if not req.resolved:
            return ""

        parts = [req.options[i].full_content for i in req.selected_indices if 0 <= i < len(req.options)]
        return "\n\n".join(parts)

    # ── Inspection ──────────────────────────────────────────────────────

    def pending_requests(self) -> list[dict[str, Any]]:
        """Return all unresolved selection requests (for the REST API)."""
        return [
            {
                "request_id": req.request_id,
                "query": req.query,
                "options": [
                    {
                        "index": o.index,
                        "preview": o.preview,
                        "source": o.source,
                    }
                    for o in req.options
                ],
                "resolved": req.resolved,
            }
            for req in self._pending.values()
            if not req.resolved
        ]

    def cleanup(self, request_id: str) -> None:
        """Remove a completed selection request."""
        self._pending.pop(request_id, None)
        self._events.pop(request_id, None)

    # ── Phase 2: Routing-keyword HITL ───────────────────────────────────

    def prepare_routing_hints(
        self,
        hints: list[RoutingKeywordHint],
        request_id: str,
    ) -> RoutingHintRequest:
        """Create a HITL request for the user to accept/reject keyword hints.

        *hints* come from :meth:`IntentRouter.extract_routing_keywords`
        applied to the WorkIQ selected content.  The user reviews which
        keywords should genuinely influence routing.
        """
        self.sweep_resolved()
        req = RoutingHintRequest(
            request_id=request_id,
            hints=hints,
        )

        if len(hints) <= 1:
            # Auto-accept when 0 or 1 hint — no point asking
            req.accepted_indices = list(range(len(hints)))
            req.resolved = True
        else:
            self._pending_hints[request_id] = req
            self._hint_events[request_id] = asyncio.Event()

        logger.info(
            "routing_hints_prepared",
            request_id=request_id,
            hint_count=len(hints),
            auto_resolved=req.resolved,
        )
        return req

    def resolve_routing_hints(self, request_id: str, accepted_indices: list[int]) -> bool:
        """User has chosen which routing hints to accept."""
        req = self._pending_hints.get(request_id)
        if not req:
            logger.warning("routing_hints_not_found", request_id=request_id)
            return False
        if req.resolved:
            logger.info("routing_hints_already_resolved", request_id=request_id)
            return False

        valid = [i for i in accepted_indices if 0 <= i < len(req.hints)]
        req.accepted_indices = valid if valid else list(range(len(req.hints)))
        req.resolved = True

        event = self._hint_events.get(request_id)
        if event:
            event.set()

        logger.info(
            "routing_hints_resolved",
            request_id=request_id,
            accepted=req.accepted_indices,
        )
        return True

    async def wait_for_routing_hints(self, request_id: str) -> RoutingHintRequest:
        """Wait until the user resolves the hint selection (or timeout)."""
        req = self._pending_hints.get(request_id)
        if not req:
            raise KeyError(f"No pending routing hints for {request_id}")

        if req.resolved:
            return req

        event = self._hint_events[request_id]
        try:
            await asyncio.wait_for(event.wait(), timeout=self._timeout)
        except TimeoutError:
            logger.warning("routing_hints_timeout", request_id=request_id)
            req.accepted_indices = list(range(len(req.hints)))
            req.resolved = True

        return req

    def accepted_routing_hints(self, request_id: str) -> list[RoutingKeywordHint]:
        """Return the accepted hints ready for the router."""
        req = self._pending_hints.get(request_id)
        if not req or not req.resolved:
            return []
        return [req.hints[i] for i in req.accepted_indices if 0 <= i < len(req.hints)]

    def pending_routing_hint_requests(self) -> list[dict[str, Any]]:
        """Return all unresolved routing-hint requests (for the REST API)."""
        return [
            {
                "request_id": req.request_id,
                "hints": [
                    {
                        "index": idx,
                        "agent_id": h.agent_id,
                        "keyword": h.keyword,
                        "matched_text": h.matched_text,
                    }
                    for idx, h in enumerate(req.hints)
                ],
                "resolved": req.resolved,
            }
            for req in self._pending_hints.values()
            if not req.resolved
        ]

    def cleanup_routing_hints(self, request_id: str) -> None:
        """Remove a completed routing-hint request."""
        self._pending_hints.pop(request_id, None)
        self._hint_events.pop(request_id, None)

    def sweep_resolved(self) -> int:
        """Remove all resolved entries to prevent memory leaks."""
        removed = 0
        for rid in [k for k, v in self._pending.items() if v.resolved]:
            self._pending.pop(rid, None)
            self._events.pop(rid, None)
            removed += 1
        for rid in [k for k, v in self._pending_hints.items() if v.resolved]:
            self._pending_hints.pop(rid, None)
            self._hint_events.pop(rid, None)
            removed += 1
        if removed:
            logger.debug("workiq_selector_swept", removed=removed)
        return removed
