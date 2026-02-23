"""HTTP Server — FastAPI app exposing the orchestrator and MCP server.

Endpoints:
- POST /chat                   — Send message to orchestrator
- POST /chat/enriched          — Send message with WorkIQ enrichment pipeline
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
- GET  /health                 — Health check
- GET  /inspector              — Agent Inspector dashboard
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

logger = structlog.get_logger(__name__)


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


class ChatResponse(BaseModel):
    response: str
    session_id: str
    routing: dict[str, Any] = {}


class WorkflowRunRequest(BaseModel):
    workflow_name: str
    params: dict[str, Any] = {}


class MCPRequestBody(BaseModel):
    jsonrpc: str = "2.0"
    method: str
    params: dict[str, Any] = {}
    id: str | int | None = None


class WorkIQQueryRequest(BaseModel):
    question: str


class WorkIQSelectRequest(BaseModel):
    request_id: str
    selected_indices: list[int]


class WorkIQAcceptHintsRequest(BaseModel):
    request_id: str
    accepted_indices: list[int]


class PlanAcceptRequest(BaseModel):
    request_id: str
    accepted_indices: list[int]


class SubPlanAcceptRequest(BaseModel):
    request_id: str
    accepted_indices: list[int]
    user_brief: str = ""


class GitHubDocumentCommitRequest(BaseModel):
    commit_sha: str = ""
    commit_message: str = ""
    diff: str = ""
    repo: str = ""


class GitHubManageIssueRequest(BaseModel):
    action: str = "create"  # create, update, close, comment
    repo: str = ""
    issue_number: int | None = None
    title: str = ""
    body: str = ""
    labels: list[str] = []
    commit_sha: str = ""


class GitHubChangelogRequest(BaseModel):
    repo: str = ""
    from_ref: str = ""
    to_ref: str = "HEAD"
    version: str = "Unreleased"


class GovernanceContextResolveRequest(BaseModel):
    request_id: str
    accepted: bool = True
    user_note: str = ""


class GovernanceSkillResolveRequest(BaseModel):
    request_id: str
    accepted: bool = True
    custom_keep: list[str] = []
    custom_overflow: list[str] = []
    override: bool = False


class GovernanceAlertResolveRequest(BaseModel):
    alert_id: str
    resolution: str = "accepted"


class GovernanceLifecycleResolveRequest(BaseModel):
    request_id: str
    accepted: bool = True
    user_note: str = ""


def create_app(
    orchestrator: Any,
    mcp_server: Any,
    catalog: Any,
    workflow_engine: Any,
    workiq_selector: Any | None = None,
    plan_selector: Any | None = None,
    governance_selector: Any | None = None,
) -> FastAPI:
    """Create the FastAPI application with all routes wired up."""

    app = FastAPI(
        title="ProtoForge",
        description="Multi-Agent Orchestrator with MCP Skills Distribution",
        version="0.1.1",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Chat Endpoint ───────────────────────────────────────────

    @app.post("/chat", response_model=ChatResponse)
    async def chat(request: ChatRequest) -> ChatResponse:
        """Send a message to the orchestrator."""
        response = await orchestrator.process(request.message)
        return ChatResponse(
            response=response,
            session_id=orchestrator.context.session_id,
        )

    @app.post("/chat/enriched", response_model=ChatResponse)
    async def chat_enriched(request: ChatRequest) -> ChatResponse:
        """Send a message through the WorkIQ-enriched pipeline.

        This triggers the full enrichment flow:
        1. Query Work IQ for organisational context
        2. HITL Phase 1 — user selects relevant content sections
        3. Extract routing keywords from selected content
        4. HITL Phase 2 — user accepts/rejects keyword hints
        5. Intent Router uses accepted hints for enriched routing

        If WorkIQ is not configured, falls back to standard routing.
        """
        response = await orchestrator.process_with_enrichment(request.message)
        return ChatResponse(
            response=response,
            session_id=orchestrator.context.session_id,
        )

    # ── MCP Endpoint ────────────────────────────────────────────

    @app.post("/mcp")
    async def mcp_endpoint(request: MCPRequestBody) -> JSONResponse:
        """MCP JSON-RPC endpoint for tool discovery and execution."""
        from src.mcp.protocol import MCPRequest

        mcp_req = MCPRequest(
            method=request.method,
            params=request.params,
            id=request.id,
        )
        mcp_resp = await mcp_server.handle_request(mcp_req)
        return JSONResponse(content=mcp_resp.to_dict())

    # ── Agent Catalog ───────────────────────────────────────────

    @app.get("/agents")
    async def list_agents() -> JSONResponse:
        """List all registered agents."""
        agents = catalog.list_agents()
        return JSONResponse(
            content=[
                {
                    "agent_type": a.agent_type,
                    "name": a.name,
                    "description": a.description,
                    "status": a.status,
                    "skills": a.skills,
                    "usage_count": a.usage_count,
                    "avg_latency_ms": round(a.avg_latency_ms, 2),
                }
                for a in agents
            ]
        )

    @app.get("/skills")
    async def list_skills() -> JSONResponse:
        """List all available skills."""
        skills = catalog.search_catalog()
        return JSONResponse(
            content=[
                {
                    "name": s.skill_name,
                    "description": s.description,
                    "agent_type": s.agent_type,
                    "version": s.version,
                    "installed": s.installed,
                    "tags": s.tags,
                }
                for s in skills
            ]
        )

    # ── Workflows ───────────────────────────────────────────────

    @app.get("/workflows")
    async def list_workflows() -> JSONResponse:
        """List all available workflows."""
        return JSONResponse(content=workflow_engine.list_workflows())

    @app.post("/workflows/run")
    async def run_workflow(request: WorkflowRunRequest) -> JSONResponse:
        """Execute a workflow by name."""
        result = await workflow_engine.execute(
            request.workflow_name,
            request.params,
        )
        return JSONResponse(content=result)

    # ── WorkIQ Human-in-the-Loop Endpoints ────────────────────────

    @app.post("/workiq/query")
    async def workiq_query(request: WorkIQQueryRequest) -> JSONResponse:
        """Send a question to Work IQ and return selection options.

        The response contains ``request_id`` and ``options`` — the caller
        should present the options to the user and POST back to
        ``/workiq/select`` with the chosen indices.
        """
        if workiq_selector is None:
            return JSONResponse(
                status_code=501,
                content={"error": "WorkIQ integration not configured"},
            )

        # Ask orchestrator to process via the workiq agent
        response = await orchestrator.process(request.question)
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

    @app.post("/workiq/select")
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
        """List pending routing-keyword hint requests (HITL Phase 2).

        After the user selects WorkIQ content sections (Phase 1), the
        system extracts routing keywords and exposes them here for the
        user to accept or reject before they influence the Intent Router.
        """
        if workiq_selector is None:
            return JSONResponse(content={"pending": []})
        return JSONResponse(content={"pending": workiq_selector.pending_routing_hint_requests()})

    @app.post("/workiq/accept-hints")
    async def workiq_accept_hints(
        request: WorkIQAcceptHintsRequest,
    ) -> JSONResponse:
        """Accept specific routing-keyword hints from WorkIQ content.

        The ``accepted_indices`` list references hint indices from the
        ``/workiq/routing-hints`` response.  Accepted hints will boost
        the corresponding agents in the Intent Router's scoring.
        """
        if workiq_selector is None:
            return JSONResponse(
                status_code=501,
                content={"error": "WorkIQ integration not configured"},
            )

        ok = workiq_selector.resolve_routing_hints(
            request.request_id,
            request.accepted_indices,
        )
        if not ok:
            return JSONResponse(
                status_code=404,
                content={
                    "error": f"No pending routing hints with id {request.request_id}",
                },
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

    # ── Plan Agent HITL Endpoints ─────────────────────────────────

    @app.get("/plan/pending")
    async def plan_pending() -> JSONResponse:
        """List pending Plan Agent suggestion reviews (Plan HITL).

        After the Plan Agent runs, its recommended sub-agents are presented
        here for the human to accept or reject before the Sub-Plan Agent
        and task agents proceed.
        """
        if plan_selector is None:
            return JSONResponse(content={"pending": []})
        return JSONResponse(content={"pending": plan_selector.pending_plan_reviews()})

    @app.post("/plan/accept")
    async def plan_accept(request: PlanAcceptRequest) -> JSONResponse:
        """Accept or reject Plan Agent suggestions.

        ``accepted_indices`` references suggestion indices from
        ``/plan/pending``.  Accepted suggestions determine which sub-agents
        will be invoked after the Sub-Plan Agent runs.
        """
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

    # ── Sub-Plan Agent HITL Endpoints ─────────────────────────────

    @app.get("/sub-plan/pending")
    async def sub_plan_pending() -> JSONResponse:
        """List pending Sub-Plan resource deployment reviews (Sub-Plan HITL).

        After the Sub-Plan Agent produces a resource deployment plan, its
        proposed resources are presented here for the human to review.
        The human can also supply a brief to override the default
        *"aim to create the minimum resources needed to demonstrate
        the functionality"*.
        """
        if plan_selector is None:
            return JSONResponse(content={"pending": []})
        return JSONResponse(content={"pending": plan_selector.pending_resource_reviews()})

    @app.post("/sub-plan/accept")
    async def sub_plan_accept(request: SubPlanAcceptRequest) -> JSONResponse:
        """Accept or reject Sub-Plan resources and optionally set a brief.

        ``accepted_indices`` references resource indices from
        ``/sub-plan/pending``.  If ``user_brief`` is provided, it overrides
        the default minimum-resource brief for this request.
        """
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

    # ── GitHub Tracker Endpoints ────────────────────────────────

    @app.post("/github/document-commit")
    async def github_document_commit(request: GitHubDocumentCommitRequest) -> JSONResponse:
        """Classify and document a git commit.

        Analyzes the commit message, diff, and metadata to produce a
        Conventional Commits message, change-type classification, scope
        detection, impact assessment, and suggested GitHub labels.
        """
        from src.agents.github_tracker_agent import GitHubTrackerAgent
        from src.orchestrator.context import ConversationContext

        ctx = ConversationContext()
        if request.repo:
            ctx.set_memory("github_repo", request.repo)

        agent = orchestrator.get_agent("github_tracker")
        if agent is None or not isinstance(agent, GitHubTrackerAgent):
            agent = GitHubTrackerAgent()

        result = await agent.execute(
            message=request.commit_message or "Document this commit",
            context=ctx,
            params={
                "action": "document_commit",
                "commit_sha": request.commit_sha,
                "commit_message": request.commit_message,
                "diff": request.diff,
                "repo": request.repo,
            },
        )
        return JSONResponse(content={"result": result.content, "artifacts": result.artifacts})

    @app.post("/github/manage-issue")
    async def github_manage_issue(request: GitHubManageIssueRequest) -> JSONResponse:
        """Create, update, close, or comment on a GitHub issue.

        Generates structured issue bodies with Conventional Commits
        classification, auto-labels, and cross-references.
        """
        from src.agents.github_tracker_agent import GitHubTrackerAgent
        from src.orchestrator.context import ConversationContext

        ctx = ConversationContext()
        if request.repo:
            ctx.set_memory("github_repo", request.repo)

        agent = orchestrator.get_agent("github_tracker")
        if agent is None or not isinstance(agent, GitHubTrackerAgent):
            agent = GitHubTrackerAgent()

        result = await agent.execute(
            message=request.body or request.title or "Manage issue",
            context=ctx,
            params={
                "action": "manage_issue",
                "issue_action": request.action,
                "repo": request.repo,
                "issue_number": request.issue_number,
                "title": request.title,
                "body": request.body,
                "labels": request.labels,
                "commit_sha": request.commit_sha,
            },
        )
        return JSONResponse(content={"result": result.content, "artifacts": result.artifacts})

    @app.post("/github/changelog")
    async def github_changelog(request: GitHubChangelogRequest) -> JSONResponse:
        """Generate a grouped changelog from commit history.

        Commits are classified by type (feat, fix, refactor, etc.) and
        grouped into Markdown sections suitable for CHANGELOG.md or
        GitHub release notes.
        """
        from src.agents.github_tracker_agent import GitHubTrackerAgent
        from src.orchestrator.context import ConversationContext

        ctx = ConversationContext()
        if request.repo:
            ctx.set_memory("github_repo", request.repo)

        agent = orchestrator.get_agent("github_tracker")
        if agent is None or not isinstance(agent, GitHubTrackerAgent):
            agent = GitHubTrackerAgent()

        result = await agent.execute(
            message="Generate changelog",
            context=ctx,
            params={
                "action": "changelog",
                "from_ref": request.from_ref,
                "to_ref": request.to_ref,
                "version": request.version,
                "repo": request.repo,
            },
        )
        return JSONResponse(content={"result": result.content, "artifacts": result.artifacts})

    # ── Governance Endpoints ───────────────────────────────────────

    @app.get("/governance/status")
    async def governance_status() -> JSONResponse:
        """Return the current governance report (token usage, alerts, skill violations)."""
        gov = getattr(orchestrator, "_governance", None)
        if gov is None:
            return JSONResponse(content={"enabled": False})
        report = gov.governance_report()
        report["enabled"] = True
        return JSONResponse(content=report)

    @app.get("/governance/alerts")
    async def governance_alerts() -> JSONResponse:
        """List all unresolved governance alerts."""
        gov = getattr(orchestrator, "_governance", None)
        if gov is None:
            return JSONResponse(content={"alerts": []})
        alerts = gov.unresolved_alerts()
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

    @app.post("/governance/resolve-alert")
    async def governance_resolve_alert(
        request: GovernanceAlertResolveRequest,
    ) -> JSONResponse:
        """Resolve a governance alert by ID."""
        gov = getattr(orchestrator, "_governance", None)
        if gov is None:
            return JSONResponse(
                status_code=501,
                content={"error": "Governance not configured"},
            )
        ok = gov.resolve_alert(request.alert_id, request.resolution)
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

    @app.get("/governance/context-reviews")
    async def governance_context_reviews() -> JSONResponse:
        """List pending context window HITL reviews."""
        if governance_selector is None:
            return JSONResponse(content={"pending": []})
        return JSONResponse(content={"pending": governance_selector.pending_context_reviews()})

    @app.post("/governance/context-reviews/resolve")
    async def governance_resolve_context(
        request: GovernanceContextResolveRequest,
    ) -> JSONResponse:
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

    @app.get("/governance/skill-reviews")
    async def governance_skill_reviews() -> JSONResponse:
        """List pending skill cap HITL reviews."""
        if governance_selector is None:
            return JSONResponse(content={"pending": []})
        return JSONResponse(content={"pending": governance_selector.pending_skill_reviews()})

    @app.post("/governance/skill-reviews/resolve")
    async def governance_resolve_skill(
        request: GovernanceSkillResolveRequest,
    ) -> JSONResponse:
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

    # ── Agent Lifecycle (HITL) ──────────────────────────────────

    @app.get("/governance/lifecycle-reviews")
    async def governance_lifecycle_reviews() -> JSONResponse:
        """List pending agent lifecycle HITL reviews (disable/remove)."""
        if governance_selector is None:
            return JSONResponse(content={"pending": []})
        return JSONResponse(content={"pending": governance_selector.pending_lifecycle_reviews()})

    @app.post("/governance/lifecycle-reviews/resolve")
    async def governance_resolve_lifecycle(
        request: GovernanceLifecycleResolveRequest,
    ) -> JSONResponse:
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

    @app.post("/agents/{agent_id}/disable")
    async def disable_agent(agent_id: str) -> JSONResponse:
        """Request disabling an agent (triggers HITL review).

        The actual disable only takes effect after the lifecycle review
        is accepted by a human operator.
        """
        try:
            result = await orchestrator.disable_agent(agent_id)
            return JSONResponse(content=result)
        except KeyError:
            return JSONResponse(
                status_code=404,
                content={"error": f"Agent '{agent_id}' not found"},
            )

    @app.post("/agents/{agent_id}/enable")
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

    @app.delete("/agents/{agent_id}")
    async def remove_agent(agent_id: str) -> JSONResponse:
        """Request removal of an agent (triggers HITL review).

        The actual removal only takes effect after the lifecycle review
        is accepted by a human operator.
        """
        try:
            result = await orchestrator.unregister_agent(agent_id)
            return JSONResponse(content=result)
        except KeyError:
            return JSONResponse(
                status_code=404,
                content={"error": f"Agent '{agent_id}' not found"},
            )

    @app.get("/agents/enabled")
    async def list_enabled_agents() -> JSONResponse:
        """List agents currently enabled for routing and dispatch."""
        return JSONResponse(content={"enabled_agents": orchestrator.list_enabled_agents()})

    @app.get("/agents/disabled")
    async def list_disabled_agents() -> JSONResponse:
        """List agents currently disabled (not routed to)."""
        return JSONResponse(content={"disabled_agents": orchestrator.list_disabled_agents()})

    # ── Health & Status ─────────────────────────────────────────

    @app.get("/health")
    async def health() -> JSONResponse:
        """Health check endpoint."""
        return JSONResponse(
            content={
                "status": "healthy",
                "orchestrator": orchestrator.get_status(),
                "mcp": mcp_server.get_status(),
                "catalog": catalog.get_status(),
            }
        )

    # ── Agent Inspector ─────────────────────────────────────────

    @app.get("/inspector", response_class=HTMLResponse)
    async def inspector() -> HTMLResponse:
        """Agent Inspector — debugging dashboard."""
        return HTMLResponse(content=INSPECTOR_HTML)

    return app


# ── Inspector Dashboard HTML ────────────────────────────────────────

INSPECTOR_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ProtoForge — Agent Inspector</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Segoe UI', system-ui, sans-serif; background: #0d1117; color: #c9d1d9; }
        .header { background: #161b22; border-bottom: 1px solid #30363d; padding: 16px 24px; }
        .header h1 { font-size: 20px; color: #58a6ff; }
        .header p { font-size: 13px; color: #8b949e; }
        .container { max-width: 1200px; margin: 0 auto; padding: 24px; }
        .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 16px; }
        .card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; }
        .card h2 { font-size: 14px; color: #58a6ff; margin-bottom: 12px; text-transform: uppercase; letter-spacing: 0.5px; }
        .stat { display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid #21262d; }
        .stat:last-child { border-bottom: none; }
        .stat-label { color: #8b949e; }
        .stat-value { color: #c9d1d9; font-weight: 600; }
        .badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 12px; }
        .badge-active { background: #1b4332; color: #3fb950; }
        .badge-error { background: #3d1f28; color: #f85149; }
        .chat-box { grid-column: span 2; }
        .chat-input { display: flex; gap: 8px; margin-top: 12px; }
        .chat-input input { flex: 1; background: #0d1117; border: 1px solid #30363d; border-radius: 6px; padding: 10px 14px; color: #c9d1d9; font-size: 14px; }
        .chat-input button { background: #238636; color: #fff; border: none; border-radius: 6px; padding: 10px 20px; cursor: pointer; }
        .chat-input button:hover { background: #2ea043; }
        #messages { max-height: 300px; overflow-y: auto; padding: 8px; }
        .msg { padding: 8px 12px; margin: 4px 0; border-radius: 6px; font-size: 14px; }
        .msg-user { background: #1f2937; }
        .msg-agent { background: #0d2137; border-left: 3px solid #58a6ff; }
        #agents-list, #skills-list { max-height: 250px; overflow-y: auto; }
        .item { padding: 8px; border-bottom: 1px solid #21262d; font-size: 13px; }
    </style>
</head>
<body>
    <div class="header">
        <h1>🔧 ProtoForge Agent Inspector</h1>
        <p>Multi-Agent Orchestrator — Debug & Monitor</p>
    </div>
    <div class="container">
        <div class="grid">
            <div class="card">
                <h2>System Status</h2>
                <div id="status">Loading...</div>
            </div>
            <div class="card">
                <h2>Registered Agents</h2>
                <div id="agents-list">Loading...</div>
            </div>
            <div class="card">
                <h2>Skill Catalog</h2>
                <div id="skills-list">Loading...</div>
            </div>
            <div class="card">
                <h2>Workflows</h2>
                <div id="workflows-list">Loading...</div>
            </div>
            <div class="card chat-box">
                <h2>Chat Console</h2>
                <div id="messages"></div>
                <div class="chat-input">
                    <input id="chat-input" placeholder="Send a message to the orchestrator..." onkeydown="if(event.key==='Enter')sendMessage()">
                    <button onclick="sendMessage()">Send</button>
                </div>
            </div>
        </div>
    </div>
    <script>
        async function loadStatus() {
            try {
                const res = await fetch('/health');
                const data = await res.json();
                const el = document.getElementById('status');
                el.innerHTML = `
                    <div class="stat"><span class="stat-label">Status</span><span class="badge badge-active">${data.status}</span></div>
                    <div class="stat"><span class="stat-label">Provider</span><span class="stat-value">${data.orchestrator.provider}</span></div>
                    <div class="stat"><span class="stat-label">Session</span><span class="stat-value">${data.orchestrator.session_id.slice(0,8)}...</span></div>
                    <div class="stat"><span class="stat-label">Messages</span><span class="stat-value">${data.orchestrator.message_count}</span></div>
                    <div class="stat"><span class="stat-label">MCP Tools</span><span class="stat-value">${data.mcp.tools_count}</span></div>
                    <div class="stat"><span class="stat-label">Installed Skills</span><span class="stat-value">${data.catalog.installed_skills}</span></div>
                `;
            } catch(e) { document.getElementById('status').textContent = 'Error loading status'; }
        }

        async function loadAgents() {
            try {
                const res = await fetch('/agents');
                const agents = await res.json();
                const el = document.getElementById('agents-list');
                el.innerHTML = agents.map(a => `
                    <div class="item">
                        <strong>${a.name}</strong> <span class="badge badge-active">${a.status}</span><br>
                        <span style="color:#8b949e">${a.description}</span><br>
                        <span style="color:#8b949e">Calls: ${a.usage_count} | Avg: ${a.avg_latency_ms}ms</span>
                    </div>
                `).join('') || '<div class="item">No agents registered</div>';
            } catch(e) { document.getElementById('agents-list').textContent = 'Error loading agents'; }
        }

        async function loadSkills() {
            try {
                const res = await fetch('/skills');
                const skills = await res.json();
                const el = document.getElementById('skills-list');
                el.innerHTML = skills.map(s => `
                    <div class="item">
                        <strong>${s.name}</strong> v${s.version} ${s.installed ? '<span class="badge badge-active">installed</span>' : ''}<br>
                        <span style="color:#8b949e">${s.description}</span>
                    </div>
                `).join('') || '<div class="item">No skills in catalog</div>';
            } catch(e) { document.getElementById('skills-list').textContent = 'Error loading skills'; }
        }

        async function loadWorkflows() {
            try {
                const res = await fetch('/workflows');
                const workflows = await res.json();
                const el = document.getElementById('workflows-list');
                el.innerHTML = workflows.map(w => `
                    <div class="item">
                        <strong>${w.name}</strong> v${w.version}<br>
                        <span style="color:#8b949e">${w.description} (${w.steps} steps)</span>
                    </div>
                `).join('') || '<div class="item">No workflows registered</div>';
            } catch(e) { document.getElementById('workflows-list').textContent = 'Error loading workflows'; }
        }

        async function sendMessage() {
            const input = document.getElementById('chat-input');
            const msg = input.value.trim();
            if (!msg) return;
            input.value = '';

            const messagesEl = document.getElementById('messages');
            messagesEl.innerHTML += `<div class="msg msg-user">🧑 ${msg}</div>`;

            try {
                const res = await fetch('/chat', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({message: msg}),
                });
                const data = await res.json();
                messagesEl.innerHTML += `<div class="msg msg-agent">🤖 ${data.response.replace(/\\n/g, '<br>')}</div>`;
                messagesEl.scrollTop = messagesEl.scrollHeight;
                loadStatus();
            } catch(e) {
                messagesEl.innerHTML += `<div class="msg msg-agent" style="border-color:#f85149">❌ Error: ${e.message}</div>`;
            }
        }

        // Init
        loadStatus();
        loadAgents();
        loadSkills();
        loadWorkflows();
        setInterval(loadStatus, 10000);
    </script>
</body>
</html>"""
