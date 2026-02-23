"""Core orchestration engine — Plan-first dispatch to subagents.

All agent registration and dispatch now uses plain ``str`` agent IDs
rather than the :class:`AgentType` enum, so forge-contributed agents
work without touching the enum.

**WorkIQ-enriched routing** (optional):

When a :class:`WorkIQClient` and :class:`WorkIQSelector` are wired in,
:meth:`process_with_enrichment` will:

1. Query Work IQ for organisational context,
2. HITL Phase 1 — let the user pick relevant content sections,
3. Extract routing keywords from the selected content,
4. HITL Phase 2 — let the user accept / reject the keyword hints,
5. Feed the accepted hints into :meth:`IntentRouter.route_with_context`.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import TYPE_CHECKING, Any

import structlog

from src.config import get_settings
from src.orchestrator.context import AgentResult, ConversationContext
from src.orchestrator.router import AgentType, IntentRouter, RoutingDecision

if TYPE_CHECKING:
    from src.agents.base import BaseAgent
    from src.workiq.client import WorkIQClient
    from src.workiq.selector import WorkIQSelector

logger = structlog.get_logger(__name__)


class OrchestratorEngine:
    """Top-level multi-agent orchestrator with Plan-first architecture.

    Every request flows through the Plan Agent first:
    1. Classifies user intent (keyword + optional LLM-based)
    2. Always dispatches to Plan Agent as the top-level coordinator
    3. Plan Agent produces a strategy and identifies required sub-agents
    4. Orchestrator fans out to the identified sub-agents in parallel
    5. Aggregates Plan + sub-agent results into a unified response
    """

    def __init__(
        self,
        workiq_client: WorkIQClient | None = None,
        workiq_selector: WorkIQSelector | None = None,
    ) -> None:
        self._router = IntentRouter()
        self._agents: dict[str, BaseAgent] = {}
        self._context = ConversationContext()
        self._settings = get_settings()
        # Optional WorkIQ enrichment layer
        self._workiq_client = workiq_client
        self._workiq_selector = workiq_selector

    # ── agent registration ──────────────────────────────────────────────

    def register_agent(self, agent_id: str, agent: BaseAgent) -> None:
        """Register a subagent under a plain string ID.

        Accepts both ``AgentType`` enum members and free-form strings so that
        forge-contributed agents can be registered without touching the enum.
        """
        self._agents[str(agent_id)] = agent
        logger.info("agent_registered", agent_id=str(agent_id), agent_class=type(agent).__name__)

    @property
    def router(self) -> IntentRouter:
        """Expose the router so callers can ``register_patterns`` at bootstrap."""
        return self._router

    @property
    def context(self) -> ConversationContext:
        return self._context

    def reset_context(self) -> None:
        """Reset conversation context for a new session."""
        self._context = ConversationContext()

    async def process(self, user_message: str) -> str:
        """Process a user message through the Plan-first orchestration pipeline.

        Flow:
        1. Add message to conversation context
        2. Route intent (keyword + optional LLM) to identify target sub-agents
        3. ALWAYS execute Plan Agent first as top-level coordinator
        4. Fan out to identified sub-agents in parallel
        5. Aggregate Plan + sub-agent results into unified response
        """
        self._context.add_user_message(user_message)

        # Step 1: Route to identify which sub-agents are relevant
        routing = self._router.route_by_keywords(user_message)
        logger.info(
            "intent_routed",
            primary=routing.primary_agent,
            confidence=routing.confidence,
            reasoning=routing.reasoning,
        )

        # Step 2: If confidence is low, try LLM-based routing
        if routing.confidence < 0.5:
            llm_routing = await self._route_with_llm(user_message)
            if llm_routing and llm_routing.confidence > routing.confidence:
                routing = llm_routing
                logger.info(
                    "llm_routing_override",
                    primary=routing.primary_agent,
                    confidence=routing.confidence,
                )

        # Step 3: ALWAYS run Plan Agent first as top-level coordinator
        plan_result = await self._dispatch(AgentType.PLAN, user_message, routing)

        # Store plan output in working memory so sub-agents can reference it
        self._context.set_memory("plan_output", plan_result.content)
        self._context.set_memory("plan_artifacts", plan_result.artifacts)

        # Step 4: Determine sub-agents to invoke (from routing, excluding PLAN)
        sub_agents = self._resolve_sub_agents(routing)
        logger.info(
            "plan_first_dispatch",
            plan_agent="plan",
            sub_agents=sub_agents,
        )

        # Step 5: Fan out to sub-agents in parallel
        sub_results: list[AgentResult] = []
        if sub_agents:
            sub_results = await self._fan_out(sub_agents, user_message, routing)

        # Step 6: Aggregate Plan + sub-agent results
        return self._aggregate(plan_result, sub_results)

    # ── WorkIQ-enriched routing ─────────────────────────────────────────

    async def process_with_enrichment(self, user_message: str) -> str:
        """Full pipeline: WorkIQ → HITL content → HITL keywords → enriched routing.

        This is the preferred entry-point when WorkIQ enrichment is desired.
        If the WorkIQ client is not configured or the query fails, the method
        falls back to the standard :meth:`process` pipeline transparently.

        Returns the same aggregated response string as :meth:`process`.
        """
        if not self._workiq_client or not self._workiq_selector:
            logger.debug("workiq_enrichment_skipped", reason="not configured")
            return await self.process(user_message)

        self._context.add_user_message(user_message)

        # Phase 0 — query Work IQ for organisational context
        workiq_result = await self._workiq_client.ask(user_message)
        if not workiq_result.ok:
            logger.warning("workiq_enrichment_failed", error=workiq_result.error)
            return await self._process_after_routing(
                user_message, self._router.route_by_keywords(user_message),
            )

        # Phase 1 — HITL: user selects relevant content sections
        content_req_id = str(uuid.uuid4())
        content_req = self._workiq_selector.prepare(workiq_result, content_req_id)

        if not content_req.resolved:
            self._context.set_memory("workiq_content_pending", content_req_id)
            content_req = await self._workiq_selector.wait_for_selection(
                content_req_id
            )

        selected_text = self._workiq_selector.selected_content(content_req_id)
        self._context.set_memory("workiq_selected_content", selected_text)

        if not selected_text:
            logger.info("workiq_enrichment_empty_selection")
            self._workiq_selector.cleanup(content_req_id)
            return await self._process_after_routing(
                user_message, self._router.route_by_keywords(user_message),
            )

        # Phase 2 — extract routing keywords from selected content
        hints = self._router.extract_routing_keywords(selected_text)

        if not hints:
            logger.info("workiq_no_routing_keywords")
            self._workiq_selector.cleanup(content_req_id)
            return await self._process_after_routing(
                user_message, self._router.route_by_keywords(user_message),
            )

        # Phase 2b — HITL: user selects which keyword hints to accept
        hint_req_id = str(uuid.uuid4())
        hint_req = self._workiq_selector.prepare_routing_hints(hints, hint_req_id)

        if not hint_req.resolved:
            self._context.set_memory("workiq_hints_pending", hint_req_id)
            hint_req = await self._workiq_selector.wait_for_routing_hints(
                hint_req_id
            )

        accepted = self._workiq_selector.accepted_routing_hints(hint_req_id)
        self._context.set_memory(
            "workiq_accepted_hints",
            [{"agent": h.agent_id, "keyword": h.keyword} for h in accepted],
        )

        # Phase 3 — enriched routing
        routing = self._router.route_with_context(user_message, accepted)
        logger.info(
            "enriched_intent_routed",
            primary=routing.primary_agent,
            confidence=routing.confidence,
            reasoning=routing.reasoning,
            hint_count=len(accepted),
        )

        # Cleanup
        self._workiq_selector.cleanup(content_req_id)
        self._workiq_selector.cleanup_routing_hints(hint_req_id)

        return await self._process_after_routing(user_message, routing)

    async def _process_after_routing(
        self, user_message: str, routing: RoutingDecision,
    ) -> str:
        """Continue the pipeline from an already-resolved routing decision.

        Shared by :meth:`process` (after its internal routing) and
        :meth:`process_with_enrichment` (after enriched routing).
        """
        # If confidence is low, try LLM-based routing
        if routing.confidence < 0.5:
            llm_routing = await self._route_with_llm(user_message)
            if llm_routing and llm_routing.confidence > routing.confidence:
                routing = llm_routing
                logger.info(
                    "llm_routing_override",
                    primary=routing.primary_agent,
                    confidence=routing.confidence,
                )

        # ALWAYS run Plan Agent first as top-level coordinator
        plan_result = await self._dispatch(AgentType.PLAN, user_message, routing)
        self._context.set_memory("plan_output", plan_result.content)
        self._context.set_memory("plan_artifacts", plan_result.artifacts)

        # Determine sub-agents to invoke (from routing, excluding PLAN)
        sub_agents = self._resolve_sub_agents(routing)
        logger.info(
            "plan_first_dispatch",
            plan_agent="plan",
            sub_agents=sub_agents,
        )

        # Fan out to sub-agents in parallel
        sub_results: list[AgentResult] = []
        if sub_agents:
            sub_results = await self._fan_out(sub_agents, user_message, routing)

        # Aggregate Plan + sub-agent results
        return self._aggregate(plan_result, sub_results)

    def _resolve_sub_agents(self, routing: RoutingDecision) -> list[str]:
        """Build the list of sub-agents to invoke after the Plan Agent.

        Collects the primary + secondary agents from routing, removes PLAN
        (since it already ran), and deduplicates.
        """
        candidates: list[str] = []

        if routing.primary_agent != AgentType.PLAN:
            candidates.append(routing.primary_agent)

        for agent_id in routing.secondary_agents:
            if agent_id != AgentType.PLAN and agent_id not in candidates:
                candidates.append(agent_id)

        return candidates

    async def _dispatch(
        self, agent_id: str, message: str, routing: RoutingDecision,
    ) -> AgentResult:
        """Dispatch to a single agent by ID."""
        agent = self._agents.get(str(agent_id))
        if not agent:
            logger.warning("agent_not_found", agent_id=str(agent_id))
            return AgentResult(
                agent_id=f"missing_{agent_id}",
                content=f"No agent registered for type '{agent_id}'.",
                confidence=0.0,
            )

        start = time.perf_counter()
        try:
            result = await agent.execute(message, self._context, routing.extracted_params)
            result.duration_ms = (time.perf_counter() - start) * 1000
            self._context.add_result(result)
            logger.info(
                "agent_completed",
                agent_id=agent.agent_id,
                duration_ms=result.duration_ms,
                confidence=result.confidence,
            )
            return result
        except Exception as exc:
            duration_ms = (time.perf_counter() - start) * 1000
            logger.error("agent_failed", agent_id=agent.agent_id, error=str(exc))
            return AgentResult(
                agent_id=agent.agent_id,
                content=f"Agent '{agent.agent_id}' encountered an error: {exc}",
                confidence=0.0,
                duration_ms=duration_ms,
            )

    async def _fan_out(
        self,
        agent_ids: list[str],
        message: str,
        routing: RoutingDecision,
    ) -> list[AgentResult]:
        """Execute multiple secondary agents in parallel."""
        tasks = [self._dispatch(aid, message, routing) for aid in agent_ids]
        return list(await asyncio.gather(*tasks, return_exceptions=False))

    async def _route_with_llm(self, message: str) -> RoutingDecision | None:
        """Use LLM for intent classification when keyword routing is uncertain."""
        prompt = self._router.get_llm_routing_prompt(message)
        logger.debug("llm_routing_requested", prompt_length=len(prompt))
        # TODO: Wire up to kernel.invoke() when LLM is configured
        return None

    def _aggregate(
        self, plan_result: AgentResult, sub_results: list[AgentResult],
    ) -> str:
        """Aggregate Plan Agent output with sub-agent results into a unified response."""
        parts = [f"**Plan:**\n{plan_result.content}"]

        for result in sub_results:
            if result.confidence > 0.3 and result.content:
                parts.append(
                    f"\n---\n**{result.agent_id} output:**\n{result.content}"
                )

        return "\n".join(parts)

    def get_status(self) -> dict[str, Any]:
        """Return current orchestrator status for health checks."""
        return {
            "session_id": self._context.session_id,
            "registered_agents": list(self._agents.keys()),
            "message_count": len(self._context.messages),
            "active_workflow": self._context.active_workflow,
            "provider": self._settings.llm.active_provider.value,
            "workiq_enrichment_available": (
                self._workiq_client is not None
                and self._workiq_selector is not None
            ),
        }
