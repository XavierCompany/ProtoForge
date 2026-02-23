"""HTTP Server — FastAPI app exposing the orchestrator and MCP server.

Endpoints:
- POST /chat              — Send message to orchestrator
- POST /mcp               — MCP JSON-RPC endpoint
- GET  /agents            — List registered agents
- GET  /skills            — List available skills
- GET  /workflows         — List available workflows
- POST /workflows/run     — Execute a workflow
- POST /workiq/query      — Send question to Work IQ, returns selection options
- GET  /workiq/pending    — List pending WorkIQ selections (HITL)
- POST /workiq/select     — Resolve a WorkIQ selection with chosen indices
- GET  /health            — Health check
- GET  /inspector         — Agent Inspector dashboard
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


def create_app(
    orchestrator: Any,
    mcp_server: Any,
    catalog: Any,
    workflow_engine: Any,
    workiq_selector: Any | None = None,
) -> FastAPI:
    """Create the FastAPI application with all routes wired up."""

    app = FastAPI(
        title="ProtoForge",
        description="Multi-Agent Orchestrator with MCP Skills Distribution",
        version="0.1.0",
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
        return JSONResponse(content=[
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
        ])

    @app.get("/skills")
    async def list_skills() -> JSONResponse:
        """List all available skills."""
        skills = catalog.search_catalog()
        return JSONResponse(content=[
            {
                "name": s.skill_name,
                "description": s.description,
                "agent_type": s.agent_type,
                "version": s.version,
                "installed": s.installed,
                "tags": s.tags,
            }
            for s in skills
        ])

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

        return JSONResponse(content={
            "response": response,
            "pending_selections": pending,
        })

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

        return JSONResponse(content={
            "request_id": request.request_id,
            "selected_content": selected,
            "status": "resolved",
        })

    # ── Health & Status ─────────────────────────────────────────

    @app.get("/health")
    async def health() -> JSONResponse:
        """Health check endpoint."""
        return JSONResponse(content={
            "status": "healthy",
            "orchestrator": orchestrator.get_status(),
            "mcp": mcp_server.get_status(),
            "catalog": catalog.get_status(),
        })

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
