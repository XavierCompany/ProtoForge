"""GitHub tracker route group."""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse

from src.orchestrator.context import ConversationContext
from src.server_models import (
    GitHubChangelogRequest,
    GitHubDocumentCommitRequest,
    GitHubManageIssueRequest,
)

_MODEL_TYPES = (
    GitHubDocumentCommitRequest,
    GitHubManageIssueRequest,
    GitHubChangelogRequest,
)


def register_github_routes(
    app: Any,
    *,
    orchestrator: Any,
    control_plane_dependencies: list[Any],
) -> None:
    """Register GitHub tracker endpoints."""

    async def _dispatch_github_tracker(
        *,
        message: str,
        params: dict[str, Any],
        repo: str | None = None,
    ) -> JSONResponse:
        """Execute GitHub tracker actions through orchestrator dispatch."""
        from src.orchestrator.router import RoutingDecision

        if not hasattr(orchestrator, "_dispatch"):
            return JSONResponse(
                status_code=501,
                content={"error": "Orchestrator dispatch is not available"},
            )

        ctx = ConversationContext()
        if repo:
            ctx.set_memory("github_repo", repo)

        routing = RoutingDecision(
            primary_agent="github_tracker",
            confidence=1.0,
            reasoning="github_endpoint_dispatch",
            extracted_params=params,
        )
        result = await orchestrator._dispatch("github_tracker", message, routing, ctx)
        return JSONResponse(content={"result": result.content, "artifacts": result.artifacts})

    @app.post("/github/document-commit", dependencies=control_plane_dependencies)
    async def github_document_commit(request: GitHubDocumentCommitRequest) -> JSONResponse:
        """Classify and document a git commit."""
        return await _dispatch_github_tracker(
            message=request.commit_message or "Document this commit",
            params={
                "action": "document_commit",
                "commit_sha": request.commit_sha,
                "commit_message": request.commit_message,
                "diff": request.diff,
                "repo": request.repo,
            },
            repo=request.repo,
        )

    @app.post("/github/manage-issue", dependencies=control_plane_dependencies)
    async def github_manage_issue(request: GitHubManageIssueRequest) -> JSONResponse:
        """Create, update, close, or comment on a GitHub issue."""
        return await _dispatch_github_tracker(
            message=request.body or request.title or "Manage issue",
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
            repo=request.repo,
        )

    @app.post("/github/changelog", dependencies=control_plane_dependencies)
    async def github_changelog(request: GitHubChangelogRequest) -> JSONResponse:
        """Generate a grouped changelog from commit history."""
        return await _dispatch_github_tracker(
            message="Generate changelog",
            params={
                "action": "changelog",
                "from_ref": request.from_ref,
                "to_ref": request.to_ref,
                "version": request.version,
                "repo": request.repo,
            },
            repo=request.repo,
        )
