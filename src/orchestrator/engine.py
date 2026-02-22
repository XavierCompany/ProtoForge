"""Core orchestration engine — dispatches to subagents based on intent routing."""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, Any

import structlog

from src.config import LLMProvider, get_settings
from src.orchestrator.context import AgentResult, ConversationContext
from src.orchestrator.router import AgentType, IntentRouter, RoutingDecision

if TYPE_CHECKING:
    from src.agents.base import BaseAgent

logger = structlog.get_logger(__name__)


class OrchestratorEngine:
    """Top-level multi-agent orchestrator.

    Switch-case router that:
    1. Classifies user intent (keyword + optional LLM-based)
    2. Dispatches to the appropriate subagent(s)
    3. Optionally fans out to secondary agents for enrichment
    4. Aggregates results and returns a unified response
    """

    def __init__(self) -> None:
        self._router = IntentRouter()
        self._agents: dict[AgentType, BaseAgent] = {}
        self._context = ConversationContext()
        self._settings = get_settings()

    def register_agent(self, agent_type: AgentType, agent: BaseAgent) -> None:
        """Register a subagent for a specific agent type."""
        self._agents[agent_type] = agent
        logger.info("agent_registered", agent_type=agent_type.value, agent_id=agent.agent_id)

    @property
    def context(self) -> ConversationContext:
        return self._context

    def reset_context(self) -> None:
        """Reset conversation context for a new session."""
        self._context = ConversationContext()

    async def process(self, user_message: str) -> str:
        """Process a user message through the orchestration pipeline.

        Flow:
        1. Add message to conversation context
        2. Route to appropriate agent(s)
        3. Execute primary agent
        4. Optionally execute secondary agents in parallel
        5. Aggregate and return response
        """
        self._context.add_user_message(user_message)

        # Step 1: Route
        routing = self._router.route_by_keywords(user_message)
        logger.info(
            "intent_routed",
            primary=routing.primary_agent.value,
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
                    primary=routing.primary_agent.value,
                    confidence=routing.confidence,
                )

        # Step 3: Dispatch to primary agent
        primary_result = await self._dispatch(routing.primary_agent, user_message, routing)

        # Step 4: Fan out to secondary agents if present
        secondary_results: list[AgentResult] = []
        if routing.secondary_agents:
            secondary_results = await self._fan_out(
                routing.secondary_agents, user_message, routing
            )

        # Step 5: Aggregate results
        return self._aggregate(primary_result, secondary_results)

    async def _dispatch(
        self, agent_type: AgentType, message: str, routing: RoutingDecision
    ) -> AgentResult:
        """Dispatch to a single agent by type (the switch-case)."""
        agent = self._agents.get(agent_type)
        if not agent:
            logger.warning("agent_not_found", agent_type=agent_type.value)
            return AgentResult(
                agent_id=f"missing_{agent_type.value}",
                content=f"No agent registered for type '{agent_type.value}'.",
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
        agent_types: list[AgentType],
        message: str,
        routing: RoutingDecision,
    ) -> list[AgentResult]:
        """Execute multiple secondary agents in parallel."""
        tasks = [self._dispatch(at, message, routing) for at in agent_types]
        return list(await asyncio.gather(*tasks, return_exceptions=False))

    async def _route_with_llm(self, message: str) -> RoutingDecision | None:
        """Use LLM for intent classification when keyword routing is uncertain."""
        # This would call the LLM with the routing prompt
        # For now, return None to use keyword routing as fallback
        prompt = self._router.get_llm_routing_prompt(message)
        logger.debug("llm_routing_requested", prompt_length=len(prompt))
        # TODO: Wire up to kernel.invoke() when LLM is configured
        return None

    def _aggregate(
        self, primary: AgentResult, secondary: list[AgentResult]
    ) -> str:
        """Aggregate results from primary and secondary agents into a unified response."""
        parts = [primary.content]

        for result in secondary:
            if result.confidence > 0.3 and result.content:
                parts.append(f"\n---\n**Additional context from {result.agent_id}:**\n{result.content}")

        return "\n".join(parts)

    def get_status(self) -> dict[str, Any]:
        """Return current orchestrator status for health checks."""
        return {
            "session_id": self._context.session_id,
            "registered_agents": [at.value for at in self._agents],
            "message_count": len(self._context.messages),
            "active_workflow": self._context.active_workflow,
            "provider": self._settings.llm.active_provider.value,
        }
