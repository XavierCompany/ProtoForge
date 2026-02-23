"""WorkIQ Agent — queries Microsoft Work IQ and presents results for selection.

This agent:
1. Receives a user question that may benefit from organisational context
   (people, meetings, emails, documents, calendar, etc.)
2. Calls ``workiq ask`` via :class:`WorkIQClient`
3. Presents the result sections to the user via :class:`WorkIQSelector`
   (human-in-the-loop — the user picks which info to use)
4. Returns only the user-approved sections as the agent result
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog

from src.agents.base import BaseAgent
from src.orchestrator.context import AgentResult, ConversationContext
from src.workiq.client import WorkIQClient
from src.workiq.selector import WorkIQSelector

logger = structlog.get_logger(__name__)

_DEFAULT_WORKIQ_PROMPT = (
    "You are the Work IQ Agent, an interface to Microsoft 365 Copilot / "
    "Work IQ.  You help users retrieve organisational context — people "
    "info, calendar events, emails, documents, Teams messages — and "
    "present the results for the user to select before feeding them into "
    "the multi-agent pipeline.\n\n"
    "Always respect data privacy: only surface information the user "
    "explicitly asked for and let them choose what to include."
)


class WorkIQAgent(BaseAgent):
    """Agent that bridges Work IQ organisational data into the orchestrator.

    Human-in-the-loop flow:
    - Query Work IQ CLI
    - Split response into selectable sections
    - Wait for user to pick relevant sections
    - Return selected content as enrichment context

    When used via the REST API the ``/workiq/pending`` endpoint exposes the
    selection options, and ``/workiq/select`` resolves them.  When used via
    the CLI, the sections are printed and the user is prompted interactively.
    """

    def __init__(
        self,
        agent_id: str = "workiq",
        description: str = "Query Microsoft Work IQ for organisational context (people, calendar, docs)",
        system_prompt: str = "",
        *,
        manifest: Any | None = None,
        client: WorkIQClient | None = None,
        selector: WorkIQSelector | None = None,
    ) -> None:
        super().__init__(
            agent_id=agent_id,
            description=description,
            system_prompt=system_prompt or _DEFAULT_WORKIQ_PROMPT,
            manifest=manifest,
        )
        self._client = client or WorkIQClient()
        self._selector = selector or WorkIQSelector()

    @property
    def client(self) -> WorkIQClient:
        return self._client

    @property
    def selector(self) -> WorkIQSelector:
        return self._selector

    async def execute(
        self,
        message: str,
        context: ConversationContext,
        _params: dict[str, Any] | None = None,
    ) -> AgentResult:
        logger.info("workiq_agent_executing", message_length=len(message))

        # 1. Query Work IQ
        result = await self._client.ask(message)

        if not result.ok:
            return AgentResult(
                agent_id=self.agent_id,
                content=(
                    f"**Work IQ query failed:** {result.error}\n\n"
                    "Ensure `workiq` is installed and you have accepted the EULA "
                    "(`workiq accept-eula`)."
                ),
                confidence=0.0,
                artifacts={"workiq_error": result.error},
            )

        # 2. Prepare human-in-the-loop selection
        request_id = str(uuid.uuid4())
        selection_req = self._selector.prepare(result, request_id)

        # Store the pending request ID in working memory so the REST
        # API / CLI can find it and present options to the user.
        context.set_memory("workiq_request_id", request_id)
        context.set_memory("workiq_raw", result.content)

        if selection_req.resolved:
            # Auto-resolved (single section or empty) — no need to wait
            selected = self._selector.selected_content(request_id)
        else:
            # Store selection options as artifacts for the Inspector / REST
            context.set_memory(
                "workiq_pending_options",
                [{"index": o.index, "preview": o.preview, "source": o.source} for o in selection_req.options],
            )

            # Wait for the user to make their selection (or timeout)
            await self._selector.wait_for_selection(request_id)
            selected = self._selector.selected_content(request_id)

        # 3. Clean up
        self._selector.cleanup(request_id)

        if not selected:
            selected = result.content  # fallback: use everything

        logger.info(
            "workiq_agent_completed",
            sections_total=len(result.sections),
            selected_length=len(selected),
        )

        return AgentResult(
            agent_id=self.agent_id,
            content=f"**Work IQ — organisational context:**\n\n{selected}",
            confidence=0.8,
            artifacts={
                "workiq_query": result.query,
                "workiq_sources": result.sources,
                "workiq_sections_total": len(result.sections),
                "workiq_request_id": request_id,
            },
        )
