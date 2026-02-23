"""Core orchestration engine — Plan-first dispatch to subagents."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

import structlog

from src.config import get_settings
from src.orchestrator.context import AgentResult, ConversationContext
from src.orchestrator.router import AgentType, IntentRouter, RoutingDecision

if TYPE_CHECKING:
    from src.agents.base import BaseAgent

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
            sub_agents=[a.value for a in sub_agents],
        )

        # Step 5: Fan out to sub-agents in parallel
        sub_results: list[AgentResult] = []
        if sub_agents:
            sub_results = await self._fan_out(sub_agents, user_message, routing)

        # Step 6: Aggregate Plan + sub-agent results
        return self._aggregate(plan_result, sub_results)

    def _resolve_sub_agents(self, routing: RoutingDecision) -> list[AgentType]:
        """Build the list of sub-agents to invoke after the Plan Agent.

        Collects the primary + secondary agents from routing, removes PLAN
        (since it already ran), and deduplicates.
        """
        candidates: list[AgentType] = []

        # Primary agent from routing (skip if it was PLAN — already executed)
        if routing.primary_agent != AgentType.PLAN:
            candidates.append(routing.primary_agent)

        # Secondary agents from routing
        for agent in routing.secondary_agents:
            if agent != AgentType.PLAN and agent not in candidates:
                candidates.append(agent)

        return candidates

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
        import json

        from src.config import LLMProvider, get_settings

        settings = get_settings()
        provider = settings.llm.active_provider

        prompt = self._router.get_llm_routing_prompt(message)
        logger.debug("llm_routing_requested", prompt_length=len(prompt))

        llm_text: str | None = None
        try:
            if provider == LLMProvider.OPENAI and settings.llm.openai_api_key:
                from openai import AsyncOpenAI

                client = AsyncOpenAI(api_key=settings.llm.openai_api_key)
                response = await client.chat.completions.create(
                    model=settings.llm.openai_model,
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"},
                )
                llm_text = response.choices[0].message.content

            elif provider == LLMProvider.AZURE_AI_FOUNDRY and settings.llm.azure_endpoint:
                from azure.identity import DefaultAzureCredential, get_bearer_token_provider
                from openai import AsyncAzureOpenAI

                token_provider = get_bearer_token_provider(
                    DefaultAzureCredential(),
                    "https://cognitiveservices.azure.com/.default",
                )
                client = AsyncAzureOpenAI(
                    azure_endpoint=settings.llm.azure_endpoint,
                    azure_deployment=settings.llm.azure_model,
                    api_version=settings.llm.azure_api_version,
                    azure_ad_token_provider=token_provider,
                )
                response = await client.chat.completions.create(
                    model=settings.llm.azure_model,
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"},
                )
                llm_text = response.choices[0].message.content

        except Exception as exc:
            logger.warning("llm_routing_failed", error=str(exc))
            return None

        if not llm_text:
            return None

        try:
            data = json.loads(llm_text)
            primary_str = data.get("primary_agent", "knowledge_base")
            primary = AgentType(primary_str)
            secondary = [AgentType(a) for a in data.get("secondary_agents", []) if a != primary_str]
            return RoutingDecision(
                primary_agent=primary,
                secondary_agents=secondary,
                confidence=float(data.get("confidence", 0.5)),
                reasoning=data.get("reasoning", "LLM routing"),
                extracted_params=data.get("extracted_params", {}),
            )
        except (json.JSONDecodeError, ValueError, KeyError) as exc:
            logger.warning("llm_routing_parse_failed", error=str(exc))
            return None

    def _aggregate(
        self, plan_result: AgentResult, sub_results: list[AgentResult]
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
            "registered_agents": [at.value for at in self._agents],
            "message_count": len(self._context.messages),
            "active_workflow": self._context.active_workflow,
            "provider": self._settings.llm.active_provider.value,
        }
