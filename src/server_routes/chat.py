"""Chat route registration helpers."""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from typing import Any

import structlog
from fastapi.responses import JSONResponse

from src.orchestrator.context import ConversationContext
from src.server_models import ChatAsyncResponse, ChatRequest

logger = structlog.get_logger(__name__)

_TASK_REVIEW_MEMORY_KEYS: tuple[str, ...] = (
    "plan_review_pending",
    "resource_review_pending",
    "governance_review_pending",
    "workiq_content_pending",
    "workiq_hints_pending",
)


def _task_pending_review_ids(task_ctx: ConversationContext | Any) -> set[str]:
    """Collect request IDs associated with a task-scoped orchestration context."""
    if not isinstance(task_ctx, ConversationContext):
        return set()

    request_ids: set[str] = set()
    for key in _TASK_REVIEW_MEMORY_KEYS:
        value = task_ctx.get_memory(key)
        if isinstance(value, str) and value:
            request_ids.add(value)
    return request_ids


def _filter_pending_reviews(
    pending_reviews: list[dict[str, Any]],
    task_request_ids: set[str],
) -> list[dict[str, Any]]:
    """Return only reviews that belong to the current task context."""
    if not task_request_ids:
        return []
    return [
        review
        for review in pending_reviews
        if isinstance(review.get("request_id"), str) and review["request_id"] in task_request_ids
    ]


def register_chat_routes(
    app: Any,
    *,
    orchestrator: Any,
    plan_selector: Any | None,
    governance_selector: Any | None,
    control_plane_dependencies: list[Any],
    chat_tasks: dict[str, dict[str, Any]],
    chat_cleanup_tasks: set[asyncio.Task[None]],
    cleanup_chat_task: Any,
) -> None:
    """Register non-blocking chat endpoints."""

    @app.post("/chat", response_model=ChatAsyncResponse, dependencies=control_plane_dependencies)
    async def chat(request: ChatRequest) -> Any:
        """Send a message to the orchestrator (non-blocking)."""
        task_id = str(uuid.uuid4())
        chat_tasks[task_id] = {
            "status": "processing",
            "created_at": time.time(),
            "message": request.message,
            "response": None,
            "session_id": request.session_id,
            "error": None,
            "context": ConversationContext(),
        }

        async def _run_chat(tid: str, msg: str) -> None:
            try:
                task_ctx = chat_tasks[tid]["context"]
                result, ctx = await orchestrator.process(msg, ctx=task_ctx)
                chat_tasks[tid]["status"] = "completed"
                chat_tasks[tid]["response"] = result
                chat_tasks[tid]["session_id"] = ctx.session_id
            except asyncio.CancelledError:
                chat_tasks[tid]["status"] = "error"
                chat_tasks[tid]["error"] = "Request cancelled"
                logger.info("chat_task_cancelled", task_id=tid)
                raise
            except Exception as exc:
                chat_tasks[tid]["status"] = "error"
                chat_tasks[tid]["error"] = str(exc)
                logger.error("chat_task_failed", task_id=tid, error=str(exc))
            finally:
                chat_tasks[tid].pop("_task_ref", None)
                task = asyncio.create_task(cleanup_chat_task(tid))
                chat_cleanup_tasks.add(task)
                task.add_done_callback(chat_cleanup_tasks.discard)

        chat_tasks[task_id]["_task_ref"] = asyncio.create_task(_run_chat(task_id, request.message))
        return ChatAsyncResponse(
            task_id=task_id,
            status="processing",
            session_id=request.session_id,
        )

    @app.get("/chat/status/{task_id}", dependencies=control_plane_dependencies)
    async def chat_status(task_id: str) -> JSONResponse:
        """Poll for the result of a non-blocking chat request."""
        task = chat_tasks.get(task_id)
        if task is None:
            return JSONResponse(
                status_code=404,
                content={"error": f"No chat task with id {task_id}"},
            )

        task_ctx = task.get("context")
        task_request_ids = _task_pending_review_ids(task_ctx)

        pending: list[dict[str, Any]] = []
        if plan_selector is not None:
            pending.extend(
                {"type": "plan", **r}
                for r in _filter_pending_reviews(plan_selector.pending_plan_reviews(), task_request_ids)
            )
            pending.extend(
                {"type": "sub_plan", **r}
                for r in _filter_pending_reviews(plan_selector.pending_resource_reviews(), task_request_ids)
            )
        if governance_selector is not None:
            pending.extend(
                {"type": "governance_context", **r}
                for r in _filter_pending_reviews(governance_selector.pending_context_reviews(), task_request_ids)
            )
            pending.extend(
                {"type": "governance_skill", **r}
                for r in _filter_pending_reviews(governance_selector.pending_skill_reviews(), task_request_ids)
            )
            pending.extend(
                {"type": "governance_lifecycle", **r}
                for r in _filter_pending_reviews(governance_selector.pending_lifecycle_reviews(), task_request_ids)
            )

        elapsed = time.time() - task["created_at"]

        phase = None
        with contextlib.suppress(Exception):
            if isinstance(task_ctx, ConversationContext):
                phase = task_ctx.get_memory("pipeline_phase")

        return JSONResponse(
            content={
                "task_id": task_id,
                "status": task["status"],
                "elapsed_seconds": round(elapsed, 1),
                "response": task["response"],
                "session_id": task["session_id"],
                "error": task["error"],
                "pending_reviews": pending,
                "pipeline_phase": phase if task["status"] == "processing" else None,
            }
        )

    @app.post("/chat/enriched", response_model=ChatAsyncResponse, dependencies=control_plane_dependencies)
    async def chat_enriched(request: ChatRequest) -> Any:
        """Send a message through the WorkIQ-enriched pipeline (non-blocking)."""
        task_id = str(uuid.uuid4())
        chat_tasks[task_id] = {
            "status": "processing",
            "created_at": time.time(),
            "message": request.message,
            "response": None,
            "session_id": request.session_id,
            "error": None,
            "context": ConversationContext(),
        }

        async def _run_enriched(tid: str, msg: str) -> None:
            try:
                task_ctx = chat_tasks[tid]["context"]
                result, ctx = await orchestrator.process_with_enrichment(msg, ctx=task_ctx)
                chat_tasks[tid]["status"] = "completed"
                chat_tasks[tid]["response"] = result
                chat_tasks[tid]["session_id"] = ctx.session_id
            except Exception as exc:
                chat_tasks[tid]["status"] = "error"
                chat_tasks[tid]["error"] = str(exc)
                logger.error("enriched_chat_task_failed", task_id=tid, error=str(exc))
            finally:
                chat_tasks[tid].pop("_task_ref", None)
                task = asyncio.create_task(cleanup_chat_task(tid))
                chat_cleanup_tasks.add(task)
                task.add_done_callback(chat_cleanup_tasks.discard)

        chat_tasks[task_id]["_task_ref"] = asyncio.create_task(_run_enriched(task_id, request.message))
        return ChatAsyncResponse(
            task_id=task_id,
            status="processing",
            session_id=request.session_id,
        )
