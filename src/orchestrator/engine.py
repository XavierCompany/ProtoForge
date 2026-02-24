"""Core orchestration engine — Plan-first dispatch to subagents.

All agent registration and dispatch now uses plain ``str`` agent IDs
rather than the :class:`AgentType` enum, so forge-contributed agents
work without touching the enum.

**Sub-Plan Agent layer** (always active):

After the Plan Agent produces a strategy the pipeline inserts two
HITL gates before task agents run:

1. **Plan HITL** — human reviews Plan Agent suggestions and keywords.
2. **Sub-Plan Agent** — creates a minimum-viable resource deployment
   plan for the prerequisite infrastructure.
3. **Sub-Plan HITL** — human reviews the resource plan (with default
   brief: *"aim to create the minimum resources needed to demonstrate
   the functionality"*).

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
from src.governance.guardian import ContextWindowExceededError
from src.orchestrator.context import AgentResult, ConversationContext
from src.orchestrator.router import AgentType, IntentRouter, RoutingDecision

if TYPE_CHECKING:
    from src.agents.base import BaseAgent
    from src.forge.context_budget import ContextBudgetManager
    from src.forge.loader import ForgeRegistry
    from src.governance.guardian import GovernanceGuardian
    from src.governance.selector import GovernanceSelector
    from src.orchestrator.plan_selector import PlanSelector
    from src.workiq.client import WorkIQClient
    from src.workiq.selector import WorkIQSelector

logger = structlog.get_logger(__name__)


class OrchestratorEngine:
    """Top-level multi-agent orchestrator with Plan-first architecture.

    Every request flows through the Plan Agent first, then through the
    Sub-Plan Agent (with HITL gates), before task agents execute:

    1. Classifies user intent (keyword + optional LLM-based)
    2. Always dispatches to Plan Agent as the top-level coordinator
    3. **Plan HITL** — human reviews plan suggestions and keywords
    4. Sub-Plan Agent plans prerequisite resources (minimum viable)
    5. **Sub-Plan HITL** — human reviews resource plan + optional brief
    6. Orchestrator fans out to the identified sub-agents in parallel
    7. Aggregates Plan + Sub-Plan + sub-agent results
    """

    def __init__(
        self,
        workiq_client: WorkIQClient | None = None,
        workiq_selector: WorkIQSelector | None = None,
        plan_selector: PlanSelector | None = None,
        governance_guardian: GovernanceGuardian | None = None,
        governance_selector: GovernanceSelector | None = None,
        budget_manager: ContextBudgetManager | None = None,
        forge_registry: ForgeRegistry | None = None,
    ) -> None:
        self._router = IntentRouter()
        self._agents: dict[str, BaseAgent] = {}
        self._disabled_agents: set[str] = set()
        self._context = ConversationContext()
        self._settings = get_settings()
        # Optional WorkIQ enrichment layer
        self._workiq_client = workiq_client
        self._workiq_selector = workiq_selector
        # Plan + Sub-Plan HITL layer
        self._plan_selector = plan_selector
        # Governance enforcement layer
        self._governance = governance_guardian
        self._governance_selector = governance_selector
        # Budget enforcement layer
        self._budget_manager = budget_manager
        self._forge_registry = forge_registry
        # Saved routing patterns for disabled agents (restore on enable)
        self._disabled_patterns: dict[str, list] = {}
        # Fan-out cap from context config (default 3)
        self._max_parallel_agents: int = 3
        if forge_registry and forge_registry.context_config:
            scaling = forge_registry.context_config.get("scaling", {})
            self._max_parallel_agents = scaling.get("max_parallel_agents", 3)

    # ── agent registration ──────────────────────────────────────────────

    def register_agent(self, agent_id: str, agent: BaseAgent) -> None:
        """Register a subagent under a plain string ID.

        Accepts both ``AgentType`` enum members and free-form strings so that
        forge-contributed agents can be registered without touching the enum.
        """
        self._agents[str(agent_id)] = agent
        logger.info("agent_registered", agent_id=str(agent_id), agent_class=type(agent).__name__)

    # ── agent lifecycle (HITL-gated) ────────────────────────────────────

    async def disable_agent(self, agent_id: str) -> dict:
        """Disable an agent with HITL confirmation.

        Prepares a lifecycle review showing which agents will remain enabled.
        Blocks until the human confirms or the review times out (fail-closed).
        Returns a status dict with the review outcome.
        """
        agent_id = str(agent_id)
        if agent_id not in self._agents:
            return {"ok": False, "error": f"Agent '{agent_id}' not registered"}
        if agent_id in self._disabled_agents:
            return {"ok": False, "error": f"Agent '{agent_id}' already disabled"}

        # Compute which agents remain enabled after this disable
        enabled_after = [aid for aid in self._agents if aid != agent_id and aid not in self._disabled_agents]

        if self._governance_selector:
            req_id = str(uuid.uuid4())
            self._governance_selector.prepare_lifecycle_review(
                req_id,
                "disable",
                agent_id,
                enabled_after,
            )
            try:
                review = await self._governance_selector.wait_for_lifecycle_review(req_id)

                if not review.accepted:
                    logger.info("disable_agent_rejected", agent_id=agent_id)
                    return {
                        "ok": False,
                        "error": "Disable rejected by human reviewer",
                        "request_id": req_id,
                    }
            finally:
                self._governance_selector.cleanup_lifecycle_review(req_id)

        self._disabled_agents.add(agent_id)
        self._disabled_patterns[agent_id] = self._router.get_patterns(agent_id)
        self._router.deregister_patterns(agent_id)
        if self._budget_manager:
            self._budget_manager.deallocate(agent_id)
        logger.info("agent_disabled", agent_id=agent_id, remaining=enabled_after)
        return {
            "ok": True,
            "agent_id": agent_id,
            "action": "disabled",
            "enabled_agents": enabled_after,
        }

    async def enable_agent(self, agent_id: str) -> dict:
        """Re-enable a previously disabled agent (no HITL needed)."""
        agent_id = str(agent_id)
        if agent_id not in self._agents:
            return {"ok": False, "error": f"Agent '{agent_id}' not registered"}
        if agent_id not in self._disabled_agents:
            return {"ok": False, "error": f"Agent '{agent_id}' is not disabled"}

        self._disabled_agents.discard(agent_id)
        saved = self._disabled_patterns.pop(agent_id, [])
        if saved:
            self._router.restore_patterns(agent_id, saved)
        logger.info("agent_enabled", agent_id=agent_id)
        return {
            "ok": True,
            "agent_id": agent_id,
            "action": "enabled",
            "enabled_agents": [aid for aid in self._agents if aid not in self._disabled_agents],
        }

    async def unregister_agent(self, agent_id: str) -> dict:
        """Remove an agent with HITL confirmation.

        Prepares a lifecycle review, blocks for human confirmation (fail-closed
        on timeout), then fully removes the agent from the engine, router, and
        budget manager.
        """
        agent_id = str(agent_id)
        if agent_id not in self._agents:
            return {"ok": False, "error": f"Agent '{agent_id}' not registered"}

        # Compute which agents remain after removal
        enabled_after = [aid for aid in self._agents if aid != agent_id and aid not in self._disabled_agents]

        if self._governance_selector:
            req_id = str(uuid.uuid4())
            self._governance_selector.prepare_lifecycle_review(
                req_id,
                "remove",
                agent_id,
                enabled_after,
            )
            try:
                review = await self._governance_selector.wait_for_lifecycle_review(req_id)

                if not review.accepted:
                    logger.info("remove_agent_rejected", agent_id=agent_id)
                    return {
                        "ok": False,
                        "error": "Remove rejected by human reviewer",
                        "request_id": req_id,
                    }
            finally:
                self._governance_selector.cleanup_lifecycle_review(req_id)

        self._agents.pop(agent_id, None)
        self._disabled_agents.discard(agent_id)
        self._router.deregister_patterns(agent_id)
        if self._budget_manager:
            self._budget_manager.deallocate(agent_id)
        logger.info("agent_unregistered", agent_id=agent_id, remaining=enabled_after)
        return {
            "ok": True,
            "agent_id": agent_id,
            "action": "removed",
            "enabled_agents": enabled_after,
        }

    def list_enabled_agents(self) -> list[str]:
        """Return IDs of all registered and enabled agents."""
        return [aid for aid in self._agents if aid not in self._disabled_agents]

    def list_disabled_agents(self) -> list[str]:
        """Return IDs of all disabled agents."""
        return sorted(self._disabled_agents)

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
        if self._governance:
            self._governance.reset_run()

    async def process(self, user_message: str) -> tuple[str, ConversationContext]:
        """Process a user message through the Plan-first orchestration pipeline.

        Flow:
        1. Add message to conversation context
        2. Route intent (keyword + optional LLM) to identify target sub-agents
        3. Delegate to ``_process_after_routing`` for Plan → Sub-Plan → fan-out → aggregate

        Returns a tuple of (response_str, context) where context is per-request.
        """
        ctx = ConversationContext()
        ctx.add_user_message(user_message)
        ctx.set_memory("pipeline_phase", "routing")
        self._context = ctx  # expose active context for status polling

        # Reset governance counters for this new orchestration run
        if self._governance:
            self._governance.reset_run()

        # Route to identify which sub-agents are relevant
        routing = self._router.route_by_keywords(user_message)
        logger.info(
            "intent_routed",
            primary=routing.primary_agent,
            confidence=routing.confidence,
            reasoning=routing.reasoning,
        )

        result = await self._process_after_routing(user_message, routing, ctx)
        return result, ctx

    # ── WorkIQ-enriched routing ─────────────────────────────────────────

    async def process_with_enrichment(self, user_message: str) -> tuple[str, ConversationContext]:
        """Full pipeline: WorkIQ → HITL content → HITL keywords → enriched routing.

        This is the preferred entry-point when WorkIQ enrichment is desired.
        If the WorkIQ client is not configured or the query fails, the method
        falls back to the standard :meth:`process` pipeline transparently.

        Returns a tuple of (response_str, context) where context is per-request.
        """
        if not self._workiq_client or not self._workiq_selector:
            logger.debug("workiq_enrichment_skipped", reason="not configured")
            return await self.process(user_message)

        ctx = ConversationContext()
        ctx.add_user_message(user_message)

        # Reset governance counters for this new orchestration run
        if self._governance:
            self._governance.reset_run()

        # Phase 0 — query Work IQ for organisational context
        workiq_result = await self._workiq_client.ask(user_message)
        if not workiq_result.ok:
            logger.warning("workiq_enrichment_failed", error=workiq_result.error)
            result = await self._process_after_routing(
                user_message,
                self._router.route_by_keywords(user_message),
                ctx,
            )
            self._context = ctx
            return result, ctx

        # Phase 1 — HITL: user selects relevant content sections
        content_req_id = str(uuid.uuid4())
        content_req = self._workiq_selector.prepare(workiq_result, content_req_id)

        try:
            if not content_req.resolved:
                ctx.set_memory("workiq_content_pending", content_req_id)
                content_req = await self._workiq_selector.wait_for_selection(content_req_id)

            selected_text = self._workiq_selector.selected_content(content_req_id)
            ctx.set_memory("workiq_selected_content", selected_text)

            if not selected_text:
                logger.info("workiq_enrichment_empty_selection")
                result = await self._process_after_routing(
                    user_message,
                    self._router.route_by_keywords(user_message),
                    ctx,
                )
                self._context = ctx
                return result, ctx

            # Phase 2 — extract routing keywords from selected content
            hints = self._router.extract_routing_keywords(selected_text)

            if not hints:
                logger.info("workiq_no_routing_keywords")
                result = await self._process_after_routing(
                    user_message,
                    self._router.route_by_keywords(user_message),
                    ctx,
                )
                self._context = ctx
                return result, ctx

            # Phase 2b — HITL: user selects which keyword hints to accept
            hint_req_id = str(uuid.uuid4())
            hint_req = self._workiq_selector.prepare_routing_hints(hints, hint_req_id)

            try:
                if not hint_req.resolved:
                    ctx.set_memory("workiq_hints_pending", hint_req_id)
                    hint_req = await self._workiq_selector.wait_for_routing_hints(hint_req_id)

                accepted = self._workiq_selector.accepted_routing_hints(hint_req_id)
                ctx.set_memory(
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

                result = await self._process_after_routing(user_message, routing, ctx)
                self._context = ctx
                return result, ctx
            finally:
                self._workiq_selector.cleanup_routing_hints(hint_req_id)
        finally:
            self._workiq_selector.cleanup(content_req_id)

    async def _process_after_routing(
        self,
        user_message: str,
        routing: RoutingDecision,
        ctx: ConversationContext,
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
        ctx.set_memory("pipeline_phase", "plan_agent")
        plan_result = await self._dispatch(AgentType.PLAN, user_message, routing, ctx)
        ctx.set_memory("plan_output", plan_result.content)
        ctx.set_memory("plan_artifacts", plan_result.artifacts)

        # Plan HITL + Sub-Plan Agent + Sub-Plan HITL
        ctx.set_memory("pipeline_phase", "plan_review")
        sub_plan_result = await self._run_sub_plan_pipeline(
            user_message,
            plan_result,
            routing,
            ctx,
        )

        # Determine sub-agents to invoke (from routing, excluding PLAN/SUB_PLAN)
        sub_agents = self._resolve_sub_agents(routing)
        # Filter by Plan HITL accepted agents if available
        plan_accepted = ctx.get_memory("plan_accepted_agents")
        if plan_accepted is not None:
            sub_agents = [a for a in sub_agents if a in plan_accepted]
        logger.info(
            "plan_first_dispatch",
            plan_agent="plan",
            sub_plan_ran=sub_plan_result is not None,
            sub_agents=sub_agents,
        )

        # Fan out to sub-agents in parallel
        ctx.set_memory("pipeline_phase", "executing_agents")
        sub_results: list[AgentResult] = []
        if sub_agents:
            sub_results = await self._fan_out(sub_agents, user_message, routing, ctx)

        # Aggregate Plan + Sub-Plan + sub-agent results
        ctx.set_memory("pipeline_phase", "complete")
        return self._aggregate(plan_result, sub_results, sub_plan_result)

    def _resolve_sub_agents(self, routing: RoutingDecision) -> list[str]:
        """Build the list of sub-agents to invoke after the Plan Agent.

        Collects the primary + secondary agents from routing, removes PLAN
        and SUB_PLAN (since they already ran), deduplicates, and enforces
        the ``max_parallel_agents`` fan-out cap from ``_context_window.yaml``.
        """
        excluded = {AgentType.PLAN, AgentType.SUB_PLAN}
        candidates: list[str] = []

        if routing.primary_agent not in excluded:
            candidates.append(routing.primary_agent)

        for agent_id in routing.secondary_agents:
            if agent_id not in excluded and agent_id not in candidates:
                candidates.append(agent_id)

        # Enforce fan-out cap — keep highest-priority candidates
        if len(candidates) > self._max_parallel_agents:
            logger.warning(
                "fan_out_cap_enforced",
                requested=len(candidates),
                cap=self._max_parallel_agents,
                dropped=candidates[self._max_parallel_agents :],
            )
            candidates = candidates[: self._max_parallel_agents]

        return candidates

    # ── Sub-Plan pipeline ───────────────────────────────────────────────

    async def _run_sub_plan_pipeline(
        self,
        user_message: str,
        plan_result: AgentResult,
        routing: RoutingDecision,
        ctx: ConversationContext,
    ) -> AgentResult | None:
        """Run Plan HITL → Sub-Plan Agent → Sub-Plan HITL.

        Returns the Sub-Plan Agent's result, or ``None`` if the sub-plan
        agent isn't registered or the PlanSelector isn't configured.
        """
        sub_plan_agent = self._agents.get(str(AgentType.SUB_PLAN))
        if not sub_plan_agent:
            logger.debug("sub_plan_skipped", reason="agent not registered")
            return None

        plan_artifacts = plan_result.artifacts or {}
        recommended = plan_artifacts.get("recommended_sub_agents", [])

        # ── Phase A: Plan HITL ──────────────────────────────────────────
        accepted_agents = recommended  # default: accept all
        plan_req_id: str | None = None
        if self._plan_selector and len(recommended) >= 1:
            plan_req_id = str(uuid.uuid4())
            plan_req = self._plan_selector.prepare_plan_review(
                plan_req_id,
                plan_result.content,
                recommended,
                plan_artifacts=plan_artifacts,
            )
            if not plan_req.resolved:
                ctx.set_memory("plan_review_pending", plan_req_id)
                plan_req = await self._plan_selector.wait_for_plan_review(plan_req_id)

            accepted_agents = self._plan_selector.accepted_plan_agents(plan_req_id)
            ctx.set_memory("plan_accepted_agents", accepted_agents)
            logger.info("plan_hitl_resolved", accepted_agents=accepted_agents)

        # ── Sub-Plan Agent execution ────────────────────────────────────
        sub_plan_result = await self._dispatch(
            AgentType.SUB_PLAN,
            user_message,
            routing,
            ctx,
        )
        ctx.set_memory("sub_plan_output", sub_plan_result.content)
        ctx.set_memory("sub_plan_artifacts", sub_plan_result.artifacts)

        # ── Phase B: Sub-Plan HITL ──────────────────────────────────────
        sub_plan_artifacts = sub_plan_result.artifacts or {}
        resources = sub_plan_artifacts.get("resources", [])
        res_req_id: str | None = None

        if self._plan_selector:
            res_req_id = str(uuid.uuid4())
            res_req = self._plan_selector.prepare_resource_review(
                res_req_id,
                sub_plan_result.content,
                resources,
                user_brief=sub_plan_artifacts.get("user_brief", ""),
            )
            if not res_req.resolved:
                ctx.set_memory("resource_review_pending", res_req_id)
                res_req = await self._plan_selector.wait_for_resource_review(res_req_id)

            accepted_resources = self._plan_selector.accepted_resources(res_req_id)
            brief = self._plan_selector.resource_brief(res_req_id)
            ctx.set_memory(
                "accepted_resources",
                [{"name": r.name, "type": r.resource_type} for r in accepted_resources],
            )
            ctx.set_memory("resource_brief", brief)
            logger.info(
                "sub_plan_hitl_resolved",
                accepted_count=len(accepted_resources),
                brief_length=len(brief),
            )

        # ── Cleanup: remove reviews after both gates resolve ────────────
        if self._plan_selector:
            if plan_req_id:
                self._plan_selector.cleanup_plan_review(plan_req_id)
            if res_req_id:
                self._plan_selector.cleanup_resource_review(res_req_id)

        return sub_plan_result

    async def _dispatch(
        self,
        agent_id: str,
        message: str,
        routing: RoutingDecision,
        ctx: ConversationContext,
    ) -> AgentResult:
        """Dispatch to a single agent by ID.

        Budget and governance checks are performed before and after execution:
        - Pre-dispatch: check disabled status, allocate per-agent budget, truncate input, context window check
        - Post-dispatch: token usage recording
        """
        # ── Disabled agent gate ────────────────────────────────────────
        if str(agent_id) in self._disabled_agents:
            logger.warning("dispatch_skipped_disabled", agent_id=str(agent_id))
            return AgentResult(
                agent_id=str(agent_id),
                content=f"Agent '{agent_id}' is currently disabled.",
                confidence=0.0,
            )

        agent = self._agents.get(str(agent_id))
        if not agent:
            logger.warning("agent_not_found", agent_id=str(agent_id))
            return AgentResult(
                agent_id=f"missing_{agent_id}",
                content=f"No agent registered for type '{agent_id}'.",
                confidence=0.0,
            )

        # ── Per-agent budget allocation & truncation ────────────────────
        effective_message = message
        if self._budget_manager:
            # Look up manifest context_budget override
            override: dict | None = None
            if self._forge_registry:
                manifest = None
                if self._forge_registry.coordinator and self._forge_registry.coordinator.id == str(agent_id):
                    manifest = self._forge_registry.coordinator
                elif str(agent_id) in self._forge_registry.agents:
                    manifest = self._forge_registry.agents[str(agent_id)]
                if manifest and manifest.context_budget:
                    override = manifest.context_budget

            # Determine agent type for default budget lookup
            agent_type = "coordinator" if str(agent_id) in (AgentType.PLAN,) else "specialist"

            self._budget_manager.allocate(str(agent_id), agent_type, override=override)

            # Truncate input to fit per-agent budget
            payload = message + (agent.system_prompt or "")
            if not self._budget_manager.fits_budget(str(agent_id), payload, direction="input"):
                effective_message = self._budget_manager.truncate(str(agent_id), message, direction="input")
                logger.info(
                    "input_truncated",
                    agent_id=str(agent_id),
                    original_tokens=self._budget_manager.count_tokens(message),
                    truncated_tokens=self._budget_manager.count_tokens(effective_message),
                )

        # ── Pre-dispatch governance check ───────────────────────────────
        # Count input tokens ONCE and reuse throughout budget + governance
        payload = effective_message + (agent.system_prompt or "")
        if self._governance:
            estimated_tokens = self._governance.count_tokens(payload)
        elif self._budget_manager:
            estimated_tokens = self._budget_manager.count_tokens(payload)
        else:
            estimated_tokens = max(1, len(payload) // 4)

        if self._governance:
            try:
                alert = self._governance.check_context_window(str(agent_id), estimated_tokens)
            except ContextWindowExceededError as exc:
                # Hard cap enforced — abort this dispatch
                logger.critical(
                    "dispatch_aborted_hard_cap",
                    agent_id=str(agent_id),
                    alert_id=exc.alert.alert_id,
                )
                if self._governance_selector:
                    await self._handle_governance_alert(exc.alert, str(agent_id), ctx)
                return AgentResult(
                    agent_id=str(agent_id),
                    content=(
                        f"Context window hard cap exceeded ({self._governance.hard_cap:,} tokens). "
                        f"Agent '{agent_id}' dispatch aborted. Decompose the task."
                    ),
                    confidence=0.0,
                )
            else:
                if alert:
                    # Warning threshold — trigger HITL but continue
                    await self._handle_governance_alert(alert, str(agent_id), ctx)

        start = time.perf_counter()
        try:
            result = await agent.execute(effective_message, ctx, routing.extracted_params)
            result.duration_ms = (time.perf_counter() - start) * 1000
            ctx.add_result(result)

            # ── Post-dispatch: record per-agent budget usage ────────────
            if self._budget_manager:
                self._budget_manager.record_usage(str(agent_id), estimated_tokens, direction="input")
                output_tokens = self._budget_manager.count_tokens(result.content)
                self._budget_manager.record_usage(str(agent_id), output_tokens, direction="output")

            # ── Post-dispatch governance recording ──────────────────────
            if self._governance:
                output_tokens = self._governance.count_tokens(result.content)
                self._governance.record_agent_usage(str(agent_id), estimated_tokens + output_tokens)

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

    async def _handle_governance_alert(
        self,
        alert: Any,
        agent_id: str,
        ctx: ConversationContext,
    ) -> None:
        """Trigger HITL for a governance alert (context window or skill cap).

        If no :class:`GovernanceSelector` is configured, logs the alert and
        continues (fail-open).
        """

        if not self._governance_selector:
            logger.warning(
                "governance_alert_no_selector",
                alert_id=alert.alert_id,
                level=alert.level,
                message=alert.message,
            )
            return

        req_id = str(uuid.uuid4())

        # Get the decomposition suggestion if available
        decomposition = self._governance.get_context_suggestion(alert.alert_id) if self._governance else None

        review = self._governance_selector.prepare_context_review(
            req_id,
            alert,
            decomposition=decomposition,
        )

        try:
            if not review.resolved:
                ctx.set_memory("governance_review_pending", req_id)
                review = await self._governance_selector.wait_for_context_review(req_id)

            if review.accepted:
                logger.info(
                    "governance_decomposition_accepted",
                    alert_id=alert.alert_id,
                    agent_id=agent_id,
                )
                ctx.set_memory("governance_decomposition_accepted", True)
            else:
                logger.info(
                    "governance_decomposition_rejected",
                    alert_id=alert.alert_id,
                    agent_id=agent_id,
                )

            # Mark alert as resolved in the guardian
            if self._governance:
                resolution = "accepted" if review.accepted else "rejected"
                self._governance.resolve_alert(alert.alert_id, resolution)
        finally:
            self._governance_selector.cleanup_context_review(req_id)

    async def _fan_out(
        self,
        agent_ids: list[str],
        message: str,
        routing: RoutingDecision,
        ctx: ConversationContext,
    ) -> list[AgentResult]:
        """Execute multiple secondary agents in parallel."""
        tasks = [self._dispatch(aid, message, routing, ctx) for aid in agent_ids]
        return list(await asyncio.gather(*tasks, return_exceptions=False))

    async def _route_with_llm(self, message: str) -> RoutingDecision | None:
        """Use LLM for intent classification when keyword routing is uncertain."""
        prompt = self._router.get_llm_routing_prompt(message)
        logger.debug("llm_routing_requested", prompt_length=len(prompt))

        from src.llm.client import get_llm_client

        response = await get_llm_client().chat(
            [
                {
                    "role": "system",
                    "content": "You are an intent classifier. Respond only in valid JSON.",
                },
                {"role": "user", "content": prompt},
            ],
            max_tokens=256,
            temperature=0.3,
        )

        if not response:
            return None

        try:
            import json

            data = json.loads(response)
            decision = RoutingDecision(
                primary_agent=data["primary_agent"],
                secondary_agents=data.get("secondary_agents", []),
                confidence=float(data.get("confidence", 0.7)),
                reasoning=data.get("reasoning", "LLM classification"),
                extracted_params=data.get("extracted_params", {}),
            )
            logger.info(
                "llm_routing_ok",
                primary=decision.primary_agent,
                confidence=decision.confidence,
            )
            return decision
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            logger.warning("llm_routing_parse_failed", error=str(exc))
            return None

    def _aggregate(
        self,
        plan_result: AgentResult,
        sub_results: list[AgentResult],
        sub_plan_result: AgentResult | None = None,
    ) -> str:
        """Aggregate Plan + Sub-Plan + sub-agent results into a unified response."""
        parts = [f"**Plan:**\n{plan_result.content}"]

        if sub_plan_result and sub_plan_result.content:
            parts.append(f"\n---\n**Sub-Plan (Resource Deployment):**\n{sub_plan_result.content}")

        parts.extend(
            f"\n---\n**{result.agent_id} output:**\n{result.content}"
            for result in sub_results
            if result.confidence > 0.3 and result.content
        )

        return "\n".join(parts)

    def get_status(self) -> dict[str, Any]:
        """Return current orchestrator status for health checks."""
        status = {
            "session_id": self._context.session_id,
            "registered_agents": list(self._agents.keys()),
            "enabled_agents": self.list_enabled_agents(),
            "disabled_agents": self.list_disabled_agents(),
            "message_count": len(self._context.messages),
            "active_workflow": self._context.active_workflow,
            "provider": self._settings.llm.active_provider.value,
            "workiq_enrichment_available": (self._workiq_client is not None and self._workiq_selector is not None),
            "governance_enabled": self._governance is not None,
        }
        if self._governance:
            status["governance"] = self._governance.governance_report()
        return status

    def get_agent(self, agent_id: str) -> BaseAgent | None:
        """Return a registered agent by ID, or ``None``."""
        return self._agents.get(str(agent_id))
