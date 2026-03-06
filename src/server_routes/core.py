"""Core route groups (MCP, catalog, workflows)."""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse

from src.server_models import MCPRequestBody, WorkflowRunRequest

_MODEL_TYPES = (MCPRequestBody, WorkflowRunRequest)


def register_core_routes(
    app: Any,
    *,
    mcp_server: Any,
    catalog: Any,
    workflow_engine: Any,
    control_plane_dependencies: list[Any],
) -> None:
    """Register MCP, catalog, and workflow endpoints."""

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

    @app.get("/workflows")
    async def list_workflows() -> JSONResponse:
        """List all available workflows."""
        return JSONResponse(content=workflow_engine.list_workflows())

    @app.post("/workflows/run", dependencies=control_plane_dependencies)
    async def run_workflow(request: WorkflowRunRequest) -> JSONResponse:
        """Execute a workflow by name."""
        result = await workflow_engine.execute(
            request.workflow_name,
            request.params,
        )
        return JSONResponse(content=result)
