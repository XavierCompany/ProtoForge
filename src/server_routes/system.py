"""System and diagnostics route groups."""

from __future__ import annotations

from typing import Any

from anyio import to_thread
from fastapi.responses import HTMLResponse, JSONResponse


def register_system_routes(
    app: Any,
    *,
    orchestrator: Any,
    mcp_server: Any,
    catalog: Any,
    templates_dir: Any,
) -> None:
    """Register health and inspector endpoints."""

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

    @app.get("/inspector", response_class=HTMLResponse)
    async def inspector() -> HTMLResponse:
        """Agent Inspector — debugging dashboard."""
        html_path = templates_dir / "inspector.html"
        html_content = await to_thread.run_sync(html_path.read_text, encoding="utf-8")
        return HTMLResponse(
            content=html_content,
            headers={"Cache-Control": "no-store"},
        )
