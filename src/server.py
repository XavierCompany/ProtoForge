"""HTTP Server — FastAPI app exposing the orchestrator and MCP server.

Endpoints:
- POST /chat                   — Send message to orchestrator (non-blocking, returns task_id)
- GET  /chat/status/{task_id}  — Poll for chat task result + pending reviews
- POST /chat/enriched          — Send message with WorkIQ enrichment (non-blocking, returns task_id)
- POST /mcp                    — MCP JSON-RPC endpoint
- GET  /agents                 — List registered agents
- GET  /skills                 — List available skills
- GET  /workflows              — List available workflows
- POST /workflows/run          — Execute a workflow
- POST /workiq/query           — Send question to Work IQ, returns selection options
- GET  /workiq/pending         — List pending WorkIQ selections (HITL Phase 1)
- POST /workiq/select          — Resolve a WorkIQ selection with chosen indices
- GET  /workiq/routing-hints   — List pending routing-keyword hints (HITL Phase 2)
- POST /workiq/accept-hints    — Accept routing-keyword hints for enriched routing
- GET  /plan/pending           — List pending Plan Agent suggestion reviews (Plan HITL)
- POST /plan/accept            — Accept/reject Plan Agent suggestions
- GET  /sub-plan/pending       — List pending Sub-Plan resource reviews (Sub-Plan HITL)
- POST /sub-plan/accept        — Accept/reject Sub-Plan resources + optional brief
- POST /github/document-commit — Classify and document a commit
- POST /github/manage-issue    — Create/update/close/comment on a GitHub issue
- POST /github/changelog       — Generate a grouped changelog from commit history
- GET  /governance/status      — Current governance report (tokens, alerts, violations)
- GET  /governance/alerts      — List unresolved governance alerts
- POST /governance/resolve-alert — Resolve a governance alert by ID
- GET  /governance/context-reviews — List pending context window HITL reviews
- POST /governance/context-reviews/resolve — Accept/reject context decomposition
- GET  /governance/skill-reviews — List pending skill cap HITL reviews
- POST /governance/skill-reviews/resolve — Accept/customise/override skill cap split
- GET  /governance/lifecycle-reviews — List pending agent lifecycle reviews (disable/remove HITL)
- POST /governance/lifecycle-reviews/resolve — Accept/reject agent disable or removal
- POST /agents/{agent_id}/disable — Request agent disable (triggers HITL review)
- POST /agents/{agent_id}/enable — Re-enable a disabled agent (no HITL required)
- DELETE /agents/{agent_id} — Request agent removal (triggers HITL review)
- GET  /agents/enabled — List currently enabled agents
- GET  /agents/disabled — List currently disabled agents
- GET  /reviews/pending        — Unified view of ALL pending HITL reviews
- GET  /health                 — Health check
- GET  /inspector              — Agent Inspector dashboard
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import structlog
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from src.server_models import (
    ChatAsyncResponse,
    ChatRequest,
    ChatResponse,
    ChatTaskState,
    GitHubChangelogRequest,
    GitHubDocumentCommitRequest,
    GitHubManageIssueRequest,
    GovernanceAlertResolveRequest,
    GovernanceContextResolveRequest,
    GovernanceLifecycleResolveRequest,
    GovernanceSkillResolveRequest,
    MCPRequestBody,
    PlanAcceptRequest,
    SubPlanAcceptRequest,
    WorkflowRunRequest,
    WorkIQAcceptHintsRequest,
    WorkIQQueryRequest,
    WorkIQSelectRequest,
)
from src.server_routes import (
    register_chat_routes,
    register_core_routes,
    register_github_routes,
    register_governance_routes,
    register_system_routes,
    register_workiq_and_plan_routes,
)

logger = structlog.get_logger(__name__)


# Background chat task tracking
_chat_tasks: dict[str, ChatTaskState] = {}
_chat_cleanup_tasks: set[asyncio.Task[None]] = set()  # prevent GC of cleanup tasks
_CHAT_TASK_TTL: float = 300.0  # seconds to keep completed tasks before eviction


async def _cleanup_chat_task(tid: str) -> None:
    """Remove a completed/errored chat task after TTL grace period."""
    await asyncio.sleep(_CHAT_TASK_TTL)
    _chat_tasks.pop(tid, None)


__all__ = [
    "ChatAsyncResponse",
    "ChatRequest",
    "ChatResponse",
    "ChatTaskState",
    "GitHubChangelogRequest",
    "GitHubDocumentCommitRequest",
    "GitHubManageIssueRequest",
    "GovernanceAlertResolveRequest",
    "GovernanceContextResolveRequest",
    "GovernanceLifecycleResolveRequest",
    "GovernanceSkillResolveRequest",
    "MCPRequestBody",
    "PlanAcceptRequest",
    "SubPlanAcceptRequest",
    "WorkIQAcceptHintsRequest",
    "WorkIQQueryRequest",
    "WorkIQSelectRequest",
    "WorkflowRunRequest",
    "_chat_tasks",
    "create_app",
]


def _has_orchestrator_method(orchestrator: Any, name: str) -> bool:
    return hasattr(type(orchestrator), name) or name in getattr(orchestrator, "__dict__", {})


class _GovernanceRouteAdapter:
    """Compatibility adapter for governance routes without mutating orchestrator."""

    def __init__(self, orchestrator: Any) -> None:
        self._orchestrator = orchestrator

    def get_governance_report(self) -> dict[str, Any] | None:
        if _has_orchestrator_method(self._orchestrator, "get_governance_report"):
            report = getattr(self._orchestrator, "get_governance_report", None)
            if callable(report):
                return report()
        return None

    def get_unresolved_governance_alerts(self) -> list[Any]:
        if _has_orchestrator_method(self._orchestrator, "get_unresolved_governance_alerts"):
            alerts = getattr(self._orchestrator, "get_unresolved_governance_alerts", None)
            if callable(alerts):
                return alerts()
        return []

    def resolve_governance_alert(self, alert_id: str, resolution: str = "accepted") -> bool:
        if _has_orchestrator_method(self._orchestrator, "resolve_governance_alert"):
            resolve = getattr(self._orchestrator, "resolve_governance_alert", None)
            if callable(resolve):
                return resolve(alert_id, resolution)
        return False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._orchestrator, name)


def _get_governance_route_orchestrator(orchestrator: Any) -> Any:
    required_methods = (
        "get_governance_report",
        "get_unresolved_governance_alerts",
        "resolve_governance_alert",
    )
    missing = [name for name in required_methods if not _has_orchestrator_method(orchestrator, name)]
    if not missing:
        return orchestrator

    logger.warning(
        "governance_route_adapter_enabled",
        missing_methods=missing,
        has_private_guardian=hasattr(orchestrator, "_governance"),
    )
    return _GovernanceRouteAdapter(orchestrator)


def create_app(
    orchestrator: Any,
    mcp_server: Any,
    catalog: Any,
    workflow_engine: Any,
    workiq_selector: Any | None = None,
    plan_selector: Any | None = None,
    governance_selector: Any | None = None,
    *,
    require_control_plane_api_key: bool = True,
    control_plane_api_key: str | None = None,
    cors_allowed_origins: list[str] | None = None,
    cors_allow_credentials: bool = True,
) -> FastAPI:
    """Create the FastAPI application and register route modules."""
    app = FastAPI(
        title="ProtoForge",
        description="Multi-Agent Orchestrator with MCP Skills Distribution",
        version="0.1.1",
    )

    effective_origins = cors_allowed_origins or ["*"]
    effective_allow_credentials = cors_allow_credentials
    if "*" in effective_origins and cors_allow_credentials:
        effective_allow_credentials = False
        logger.warning(
            "cors_credentials_disabled_for_wildcard_origin",
            reason="wildcard origins cannot be used with credentialed requests safely",
        )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=effective_origins,
        allow_credentials=effective_allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    control_plane_dependencies: list[Any] = []
    if require_control_plane_api_key:

        async def _require_control_plane_access(
            x_api_key: str | None = Header(default=None, alias="X-API-Key"),
        ) -> None:
            if not control_plane_api_key:
                logger.error("control_plane_auth_misconfigured")
                raise HTTPException(
                    status_code=503,
                    detail="Control-plane authentication is misconfigured",
                )
            if x_api_key != control_plane_api_key:
                raise HTTPException(status_code=401, detail="Invalid or missing API key")

        control_plane_dependencies = [Depends(_require_control_plane_access)]

    register_chat_routes(
        app,
        orchestrator=orchestrator,
        plan_selector=plan_selector,
        governance_selector=governance_selector,
        control_plane_dependencies=control_plane_dependencies,
        chat_tasks=_chat_tasks,
        chat_cleanup_tasks=_chat_cleanup_tasks,
        cleanup_chat_task=_cleanup_chat_task,
    )

    register_core_routes(
        app,
        mcp_server=mcp_server,
        catalog=catalog,
        workflow_engine=workflow_engine,
        control_plane_dependencies=control_plane_dependencies,
    )

    register_workiq_and_plan_routes(
        app,
        orchestrator=orchestrator,
        workiq_selector=workiq_selector,
        plan_selector=plan_selector,
        control_plane_dependencies=control_plane_dependencies,
    )

    register_github_routes(
        app,
        orchestrator=orchestrator,
        control_plane_dependencies=control_plane_dependencies,
    )

    governance_route_orchestrator = _get_governance_route_orchestrator(orchestrator)
    register_governance_routes(
        app,
        orchestrator=governance_route_orchestrator,
        governance_selector=governance_selector,
        plan_selector=plan_selector,
        workiq_selector=workiq_selector,
        control_plane_dependencies=control_plane_dependencies,
    )

    register_system_routes(
        app,
        orchestrator=orchestrator,
        mcp_server=mcp_server,
        catalog=catalog,
        templates_dir=Path(__file__).parent / "templates",
    )

    return app
