"""WorkIQ and Plan/Sub-Plan HITL route groups."""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse

from src.server_models import (
    PlanAcceptRequest,
    SubPlanAcceptRequest,
    WorkIQAcceptHintsRequest,
    WorkIQQueryRequest,
    WorkIQSelectRequest,
)

_MODEL_TYPES = (
    WorkIQQueryRequest,
    WorkIQSelectRequest,
    WorkIQAcceptHintsRequest,
    PlanAcceptRequest,
    SubPlanAcceptRequest,
)


def register_workiq_and_plan_routes(
    app: Any,
    *,
    orchestrator: Any,
    workiq_selector: Any | None,
    plan_selector: Any | None,
    control_plane_dependencies: list[Any],
) -> None:
    """Register WorkIQ and Plan/Sub-Plan HITL endpoints."""

    @app.post("/workiq/query")
    async def workiq_query(request: WorkIQQueryRequest) -> JSONResponse:
        """Send a question to Work IQ and return selection options."""
        if workiq_selector is None:
            return JSONResponse(
                status_code=501,
                content={"error": "WorkIQ integration not configured"},
            )

        response, _ctx = await orchestrator.process(request.question)
        pending = workiq_selector.pending_requests()
        return JSONResponse(
            content={
                "response": response,
                "pending_selections": pending,
            }
        )

    @app.get("/workiq/pending")
    async def workiq_pending() -> JSONResponse:
        """List pending WorkIQ selection requests awaiting user input."""
        if workiq_selector is None:
            return JSONResponse(content={"pending": []})
        return JSONResponse(content={"pending": workiq_selector.pending_requests()})

    @app.post("/workiq/select", dependencies=control_plane_dependencies)
    async def workiq_select(request: WorkIQSelectRequest) -> JSONResponse:
        """Resolve a pending selection — user picks which sections to use."""
        if workiq_selector is None:
            return JSONResponse(
                status_code=501,
                content={"error": "WorkIQ integration not configured"},
            )

        ok = workiq_selector.resolve(request.request_id, request.selected_indices)
        if not ok:
            return JSONResponse(
                status_code=404,
                content={"error": f"No pending selection with id {request.request_id}"},
            )

        selected = workiq_selector.selected_content(request.request_id)
        workiq_selector.cleanup(request.request_id)
        return JSONResponse(
            content={
                "request_id": request.request_id,
                "selected_content": selected,
                "status": "resolved",
            }
        )

    @app.get("/workiq/routing-hints")
    async def workiq_routing_hints() -> JSONResponse:
        """List pending routing-keyword hint requests (HITL Phase 2)."""
        if workiq_selector is None:
            return JSONResponse(content={"pending": []})
        return JSONResponse(content={"pending": workiq_selector.pending_routing_hint_requests()})

    @app.post("/workiq/accept-hints", dependencies=control_plane_dependencies)
    async def workiq_accept_hints(request: WorkIQAcceptHintsRequest) -> JSONResponse:
        """Accept specific routing-keyword hints from WorkIQ content."""
        if workiq_selector is None:
            return JSONResponse(
                status_code=501,
                content={"error": "WorkIQ integration not configured"},
            )

        ok = workiq_selector.resolve_routing_hints(request.request_id, request.accepted_indices)
        if not ok:
            return JSONResponse(
                status_code=404,
                content={"error": f"No pending routing hints with id {request.request_id}"},
            )

        accepted = workiq_selector.accepted_routing_hints(request.request_id)
        workiq_selector.cleanup_routing_hints(request.request_id)
        return JSONResponse(
            content={
                "request_id": request.request_id,
                "accepted_hints": [{"agent_id": h.agent_id, "keyword": h.keyword} for h in accepted],
                "status": "resolved",
            }
        )

    @app.get("/plan/pending")
    async def plan_pending() -> JSONResponse:
        """List pending Plan Agent suggestion reviews (Plan HITL)."""
        if plan_selector is None:
            return JSONResponse(content={"pending": []})
        return JSONResponse(content={"pending": plan_selector.pending_plan_reviews()})

    @app.post("/plan/accept", dependencies=control_plane_dependencies)
    async def plan_accept(request: PlanAcceptRequest) -> JSONResponse:
        """Accept or reject Plan Agent suggestions."""
        if plan_selector is None:
            return JSONResponse(
                status_code=501,
                content={"error": "Plan HITL not configured"},
            )

        ok = plan_selector.resolve_plan_review(
            request.request_id,
            request.accepted_indices,
        )
        if not ok:
            return JSONResponse(
                status_code=404,
                content={"error": f"No pending plan review with id {request.request_id}"},
            )

        accepted = plan_selector.accepted_plan_agents(request.request_id)
        return JSONResponse(
            content={
                "request_id": request.request_id,
                "accepted_agents": accepted,
                "status": "resolved",
            }
        )

    @app.get("/sub-plan/pending")
    async def sub_plan_pending() -> JSONResponse:
        """List pending Sub-Plan resource deployment reviews (Sub-Plan HITL)."""
        if plan_selector is None:
            return JSONResponse(content={"pending": []})
        return JSONResponse(content={"pending": plan_selector.pending_resource_reviews()})

    @app.post("/sub-plan/accept", dependencies=control_plane_dependencies)
    async def sub_plan_accept(request: SubPlanAcceptRequest) -> JSONResponse:
        """Accept or reject Sub-Plan resources and optionally set a brief."""
        if plan_selector is None:
            return JSONResponse(
                status_code=501,
                content={"error": "Sub-Plan HITL not configured"},
            )

        ok = plan_selector.resolve_resource_review(
            request.request_id,
            request.accepted_indices,
            user_brief=request.user_brief,
        )
        if not ok:
            return JSONResponse(
                status_code=404,
                content={"error": f"No pending resource review with id {request.request_id}"},
            )

        accepted = plan_selector.accepted_resources(request.request_id)
        brief = plan_selector.resource_brief(request.request_id)
        return JSONResponse(
            content={
                "request_id": request.request_id,
                "accepted_resources": [
                    {"name": r.name, "type": r.resource_type, "purpose": r.purpose} for r in accepted
                ],
                "user_brief": brief,
                "status": "resolved",
            }
        )
