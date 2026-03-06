"""Governance, lifecycle, and unified review route groups."""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse

from src.server_models import (
    GovernanceAlertResolveRequest,
    GovernanceContextResolveRequest,
    GovernanceLifecycleResolveRequest,
    GovernanceSkillResolveRequest,
)

_MODEL_TYPES = (
    GovernanceAlertResolveRequest,
    GovernanceContextResolveRequest,
    GovernanceSkillResolveRequest,
    GovernanceLifecycleResolveRequest,
)


def register_governance_routes(
    app: Any,
    *,
    orchestrator: Any,
    governance_selector: Any | None,
    plan_selector: Any | None,
    workiq_selector: Any | None,
    control_plane_dependencies: list[Any],
) -> None:
    """Register governance, lifecycle, and unified reviews endpoints."""

    @app.get("/governance/status", dependencies=control_plane_dependencies)
    async def governance_status() -> JSONResponse:
        """Return the current governance report (token usage, alerts, skill violations)."""
        report = orchestrator.get_governance_report()
        if report is None:
            return JSONResponse(content={"enabled": False})
        report["enabled"] = True
        return JSONResponse(content=report)

    @app.get("/governance/alerts", dependencies=control_plane_dependencies)
    async def governance_alerts() -> JSONResponse:
        """List all unresolved governance alerts."""
        alerts = orchestrator.get_unresolved_governance_alerts()

        return JSONResponse(
            content={
                "alerts": [
                    {
                        "alert_id": a.alert_id,
                        "category": a.category,
                        "level": a.level,
                        "agent_id": a.agent_id,
                        "message": a.message,
                        "suggestion": a.suggestion,
                        "details": a.details,
                    }
                    for a in alerts
                ]
            }
        )

    @app.post("/governance/resolve-alert", dependencies=control_plane_dependencies)
    async def governance_resolve_alert(request: GovernanceAlertResolveRequest) -> JSONResponse:
        """Resolve a governance alert by ID."""
        if orchestrator.get_governance_report() is None:
            return JSONResponse(
                status_code=501,
                content={"error": "Governance not configured"},
            )
        ok = orchestrator.resolve_governance_alert(request.alert_id, request.resolution)

        if not ok:
            return JSONResponse(
                status_code=404,
                content={"error": f"No unresolved alert with id {request.alert_id}"},
            )
        return JSONResponse(
            content={
                "alert_id": request.alert_id,
                "resolution": request.resolution,
                "status": "resolved",
            }
        )

    @app.get("/governance/context-reviews", dependencies=control_plane_dependencies)
    async def governance_context_reviews() -> JSONResponse:
        """List pending context window HITL reviews."""
        if governance_selector is None:
            return JSONResponse(content={"pending": []})
        return JSONResponse(content={"pending": governance_selector.pending_context_reviews()})

    @app.post("/governance/context-reviews/resolve", dependencies=control_plane_dependencies)
    async def governance_resolve_context(request: GovernanceContextResolveRequest) -> JSONResponse:
        """Accept or reject a context window decomposition suggestion."""
        if governance_selector is None:
            return JSONResponse(
                status_code=501,
                content={"error": "Governance HITL not configured"},
            )
        ok = governance_selector.resolve_context_review(
            request.request_id,
            request.accepted,
            user_note=request.user_note,
        )
        if not ok:
            return JSONResponse(
                status_code=404,
                content={"error": f"No pending context review with id {request.request_id}"},
            )
        return JSONResponse(
            content={
                "request_id": request.request_id,
                "accepted": request.accepted,
                "status": "resolved",
            }
        )

    @app.get("/governance/skill-reviews", dependencies=control_plane_dependencies)
    async def governance_skill_reviews() -> JSONResponse:
        """List pending skill cap HITL reviews."""
        if governance_selector is None:
            return JSONResponse(content={"pending": []})
        return JSONResponse(content={"pending": governance_selector.pending_skill_reviews()})

    @app.post("/governance/skill-reviews/resolve", dependencies=control_plane_dependencies)
    async def governance_resolve_skill(request: GovernanceSkillResolveRequest) -> JSONResponse:
        """Accept, customise, or override a skill cap violation."""
        if governance_selector is None:
            return JSONResponse(
                status_code=501,
                content={"error": "Governance HITL not configured"},
            )
        ok = governance_selector.resolve_skill_review(
            request.request_id,
            request.accepted,
            custom_keep=request.custom_keep or None,
            custom_overflow=request.custom_overflow or None,
            override=request.override,
        )
        if not ok:
            return JSONResponse(
                status_code=404,
                content={"error": f"No pending skill review with id {request.request_id}"},
            )
        return JSONResponse(
            content={
                "request_id": request.request_id,
                "accepted": request.accepted,
                "overridden": request.override,
                "status": "resolved",
            }
        )

    @app.get("/governance/lifecycle-reviews", dependencies=control_plane_dependencies)
    async def governance_lifecycle_reviews() -> JSONResponse:
        """List pending agent lifecycle HITL reviews (disable/remove)."""
        if governance_selector is None:
            return JSONResponse(content={"pending": []})
        return JSONResponse(content={"pending": governance_selector.pending_lifecycle_reviews()})

    @app.post("/governance/lifecycle-reviews/resolve", dependencies=control_plane_dependencies)
    async def governance_resolve_lifecycle(request: GovernanceLifecycleResolveRequest) -> JSONResponse:
        """Accept or reject an agent lifecycle action (disable/remove)."""
        if governance_selector is None:
            return JSONResponse(
                status_code=501,
                content={"error": "Governance HITL not configured"},
            )
        ok = governance_selector.resolve_lifecycle_review(
            request.request_id,
            request.accepted,
            user_note=request.user_note,
        )
        if not ok:
            return JSONResponse(
                status_code=404,
                content={"error": f"No pending lifecycle review with id {request.request_id}"},
            )
        return JSONResponse(
            content={
                "request_id": request.request_id,
                "accepted": request.accepted,
                "status": "resolved",
            }
        )

    @app.post("/agents/{agent_id}/disable", dependencies=control_plane_dependencies)
    async def disable_agent(agent_id: str) -> JSONResponse:
        """Request disabling an agent (triggers HITL review)."""
        try:
            result = await orchestrator.disable_agent(agent_id)
            return JSONResponse(content=result)
        except KeyError:
            return JSONResponse(
                status_code=404,
                content={"error": f"Agent '{agent_id}' not found"},
            )

    @app.post("/agents/{agent_id}/enable", dependencies=control_plane_dependencies)
    async def enable_agent(agent_id: str) -> JSONResponse:
        """Re-enable a previously disabled agent (no HITL required)."""
        try:
            result = await orchestrator.enable_agent(agent_id)
            return JSONResponse(content=result)
        except KeyError:
            return JSONResponse(
                status_code=404,
                content={"error": f"Agent '{agent_id}' not found"},
            )

    @app.delete("/agents/{agent_id}", dependencies=control_plane_dependencies)
    async def remove_agent(agent_id: str) -> JSONResponse:
        """Request removal of an agent (triggers HITL review)."""
        try:
            result = await orchestrator.unregister_agent(agent_id)
            return JSONResponse(content=result)
        except KeyError:
            return JSONResponse(
                status_code=404,
                content={"error": f"Agent '{agent_id}' not found"},
            )

    @app.get("/agents/enabled", dependencies=control_plane_dependencies)
    async def list_enabled_agents() -> JSONResponse:
        """List agents currently enabled for routing and dispatch."""
        return JSONResponse(content={"enabled_agents": orchestrator.list_enabled_agents()})

    @app.get("/agents/disabled", dependencies=control_plane_dependencies)
    async def list_disabled_agents() -> JSONResponse:
        """List agents currently disabled (not routed to)."""
        return JSONResponse(content={"disabled_agents": orchestrator.list_disabled_agents()})

    @app.get("/reviews/pending", dependencies=control_plane_dependencies)
    async def reviews_pending() -> JSONResponse:
        """Unified view of all pending HITL reviews across every gate."""
        reviews: list[dict[str, Any]] = []

        if plan_selector is not None:
            reviews.extend({"type": "plan", **r} for r in plan_selector.pending_plan_reviews())
            reviews.extend({"type": "sub_plan", **r} for r in plan_selector.pending_resource_reviews())

        if workiq_selector is not None:
            reviews.extend({"type": "workiq", **r} for r in workiq_selector.pending_requests())
            reviews.extend({"type": "workiq_hints", **r} for r in workiq_selector.pending_routing_hint_requests())

        if governance_selector is not None:
            reviews.extend({"type": "governance_context", **r} for r in governance_selector.pending_context_reviews())
            reviews.extend({"type": "governance_skill", **r} for r in governance_selector.pending_skill_reviews())
            reviews.extend(
                {"type": "governance_lifecycle", **r} for r in governance_selector.pending_lifecycle_reviews()
            )

        return JSONResponse(content={"pending": reviews, "count": len(reviews)})
