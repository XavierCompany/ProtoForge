"""Shared FastAPI request/response models for HTTP server routes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypedDict

from pydantic import BaseModel

if TYPE_CHECKING:
    from src.orchestrator.context import ConversationContext


class ChatRequest(BaseModel):
    """Inbound chat message from a user — the primary API input."""

    message: str
    session_id: str | None = None


class ChatResponse(BaseModel):
    """Orchestrator response containing the aggregated agent output."""

    response: str
    session_id: str
    routing: dict[str, Any] = {}


class ChatAsyncResponse(BaseModel):
    """Non-blocking chat response — references a background task."""

    task_id: str
    status: str = "processing"  # processing | completed | error
    message: str = "Request is being processed. Poll GET /chat/status/{task_id} for updates."
    response: str | None = None
    session_id: str | None = None
    pending_reviews: list[dict[str, Any]] = []


class ChatTaskState(TypedDict, total=False):
    """In-memory state for a non-blocking chat request."""

    status: str
    created_at: float
    message: str
    response: str | None
    session_id: str | None
    error: str | None
    context: ConversationContext
    _task_ref: Any


class WorkflowRunRequest(BaseModel):
    """Request to execute a named workflow from the forge/ ecosystem."""

    workflow_name: str
    params: dict[str, Any] = {}


class MCPRequestBody(BaseModel):
    """JSON-RPC 2.0 envelope for MCP protocol messages."""

    jsonrpc: str = "2.0"
    method: str
    params: dict[str, Any] = {}
    id: str | int | None = None


class WorkIQQueryRequest(BaseModel):
    """Request to query Microsoft Work IQ for organisational context."""

    question: str


class WorkIQSelectionOption(BaseModel):
    """A selectable WorkIQ section preview presented during HITL review."""

    index: int
    preview: str
    source: str = ""


class WorkIQPendingSelection(BaseModel):
    """One pending WorkIQ content-selection request."""

    request_id: str
    query: str
    options: list[WorkIQSelectionOption] = []
    resolved: bool = False


class WorkIQQueryResponse(BaseModel):
    """Response payload for `/workiq/query` with request-scoped selection context."""

    response: str
    request_id: str | None = None
    sections: list[WorkIQSelectionOption] = []
    pending_selections: list[WorkIQPendingSelection] = []


class WorkIQSelectRequest(BaseModel):
    """Human selection of WorkIQ results to include in the pipeline."""

    request_id: str
    selected_indices: list[int]


class WorkIQAcceptHintsRequest(BaseModel):
    """Human acceptance of WorkIQ routing hints for agent dispatch."""

    request_id: str
    accepted_indices: list[int]


class PlanAcceptRequest(BaseModel):
    """Human acceptance of Plan Agent suggestions — selects which sub-agents proceed."""

    request_id: str
    accepted_indices: list[int]


class SubPlanAcceptRequest(BaseModel):
    """Human acceptance of Sub-Plan resources, with optional brief override."""

    request_id: str
    accepted_indices: list[int]
    user_brief: str = ""


class GitHubDocumentCommitRequest(BaseModel):
    """Request to classify and document a git commit (Conventional Commits)."""

    commit_sha: str = ""
    commit_message: str = ""
    diff: str = ""
    repo: str = ""


class GitHubManageIssueRequest(BaseModel):
    """Request to create, update, close, or comment on a GitHub issue."""

    action: str = "create"  # create, update, close, comment
    repo: str = ""
    issue_number: int | None = None
    title: str = ""
    body: str = ""
    labels: list[str] = []
    commit_sha: str = ""


class GitHubChangelogRequest(BaseModel):
    """Request to generate a grouped changelog from commit history."""

    repo: str = ""
    from_ref: str = ""
    to_ref: str = "HEAD"
    version: str = "0.1.1"


class GovernanceContextResolveRequest(BaseModel):
    """Accept or reject a context-window decomposition suggestion."""

    request_id: str
    accepted: bool = True
    user_note: str = ""


class GovernanceSkillResolveRequest(BaseModel):
    """Accept, customise, or override a skill-cap violation split."""

    request_id: str
    accepted: bool = True
    custom_keep: list[str] = []
    custom_overflow: list[str] = []
    override: bool = False


class GovernanceAlertResolveRequest(BaseModel):
    """Resolve an individual governance alert by ID."""

    alert_id: str
    resolution: str = "accepted"


class GovernanceLifecycleResolveRequest(BaseModel):
    """Accept or reject an agent lifecycle action (disable / remove)."""

    request_id: str
    accepted: bool = True
    user_note: str = ""
