"""Sub-Plan Agent — infrastructure and resource planning.

Invoked **after** the Plan Agent and **before** any task agents.
Its job is to examine the Plan Agent's strategy and identify the
minimum prerequisite resources (infra, connectors, APIs, services)
required to demonstrate the planned functionality.

Both the Plan Agent's suggestions and the Sub-Plan's resource plan
pass through a HITL gate so the human can accept, modify or reject
each proposed agent suggestion / resource before execution proceeds.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from src.agents.base import BaseAgent
from src.orchestrator.context import AgentResult, ConversationContext

if TYPE_CHECKING:
    from src.forge.loader import AgentManifest

logger = structlog.get_logger(__name__)

_DEFAULT_SUB_PLAN_PROMPT = """\
You are the Sub-Plan Agent — the infrastructure and resource planner.

You are invoked AFTER the Plan Agent and BEFORE task agents. Your job is to
identify the **minimum prerequisite resources** needed to demonstrate the
functionality described by the Plan Agent.

Key principle:
> You should aim to create the minimum resources needed to demonstrate
> the functionality as an example.

For every resource, specify:
1. Resource name/type
2. Purpose (why it's needed)
3. Estimated effort (quick / moderate / complex)
4. Dependencies

Always prefer free tiers, local emulators, and default configurations.
Do NOT propose production-grade setups.
"""


class SubPlanAgent(BaseAgent):
    """Infrastructure / resource planner that sits between Plan and task agents.

    Can be created two ways:

    * ``SubPlanAgent()`` — uses the built-in fallback prompt (tests, legacy).
    * ``SubPlanAgent.from_manifest(manifest)`` — reads prompt from forge/.
    """

    def __init__(
        self,
        agent_id: str = "sub_plan",
        description: str = (
            "Plans prerequisite resources (infra, connectors, APIs) required "
            "to demonstrate the planned functionality — minimum viable resources only"
        ),
        system_prompt: str = _DEFAULT_SUB_PLAN_PROMPT,
        *,
        manifest: AgentManifest | None = None,
    ) -> None:
        super().__init__(
            agent_id=agent_id,
            description=description,
            system_prompt=system_prompt,
            manifest=manifest,
        )

    async def execute(
        self,
        message: str,
        context: ConversationContext,
        params: dict[str, Any] | None = None,
    ) -> AgentResult:
        logger.info(
            "sub_plan_agent_executing",
            message_length=len(message),
            has_plan_output=bool(context.get_memory("plan_output")),
        )

        self._build_messages(message, context)

        # Read the Plan Agent's output from working memory
        plan_output: str = context.get_memory("plan_output", "")
        plan_artifacts: dict = context.get_memory("plan_artifacts", {})
        recommended_agents: list[str] = plan_artifacts.get("recommended_sub_agents", [])
        user_brief: str = (params or {}).get("user_brief", "")

        # Identify resources each recommended agent might need
        resources = self._identify_resources(message, recommended_agents, plan_output)

        # Build the resource-plan response
        resource_lines = []
        for idx, res in enumerate(resources, 1):
            deps = ", ".join(res["dependencies"]) if res["dependencies"] else "none"
            resource_lines.append(
                f"  {idx}. **{res['name']}** ({res['type']})\n"
                f"     Purpose: {res['purpose']}\n"
                f"     Effort: {res['effort']} | Dependencies: {deps}"
            )

        resource_block = "\n".join(resource_lines) if resource_lines else "  (no extra resources identified)"

        brief_note = ""
        if user_brief:
            brief_note = f"\n\n**Human brief:** {user_brief}\n"

        plan_response = (
            f"**Sub-Plan Agent — Resource Deployment Plan**\n\n"
            f"Based on the Plan Agent's strategy, here are the prerequisite "
            f"resources needed before task agents can execute:\n{brief_note}\n"
            f"**Resources:**\n{resource_block}\n\n"
            f"**Deployment order:** "
            f"{' → '.join(r['name'] for r in resources) or 'n/a'}\n\n"
            f"**Principle:** _Deploy the minimum resources needed to "
            f"demonstrate the functionality as an example._\n\n"
            f"_Waiting for human review before proceeding to task agents._"
        )

        return AgentResult(
            agent_id=self.agent_id,
            content=plan_response,
            confidence=0.80,
            artifacts={
                "resource_count": len(resources),
                "resources": resources,
                "recommended_sub_agents": recommended_agents,
                "user_brief": user_brief,
            },
        )

    # ── internal helpers ────────────────────────────────────────────────

    @staticmethod
    def _identify_resources(
        message: str,
        recommended_agents: list[str],
        plan_output: str,
    ) -> list[dict[str, Any]]:
        """Identify prerequisite resources based on the task context.

        This uses keyword heuristics on the user message and plan output.
        When an LLM backend is wired in, this will be replaced by a model
        call that produces the resource list from the plan.
        """
        resources: list[dict[str, Any]] = []
        combined = f"{message} {plan_output}".lower()

        # Connector / workspace resources
        if any(kw in combined for kw in ["connector", "workspace", "integration", "m365"]):
            resources.append(
                {
                    "name": "Workspace Connector",
                    "type": "connector",
                    "purpose": "Establish connection to external service (e.g. M365, Jira, GitHub)",
                    "effort": "moderate",
                    "dependencies": [],
                }
            )

        # Storage / database resources
        if any(kw in combined for kw in ["storage", "blob", "database", "sql", "cosmos", "data"]):
            resources.append(
                {
                    "name": "Storage Account (dev tier)",
                    "type": "azure-storage",
                    "purpose": "Provide blob/table storage for agent data",
                    "effort": "quick",
                    "dependencies": [],
                }
            )

        # API / service resources
        if any(kw in combined for kw in ["api", "endpoint", "service", "function", "http"]):
            resources.append(
                {
                    "name": "API Endpoint (local or dev)",
                    "type": "api-service",
                    "purpose": "Expose HTTP endpoint for the demonstrated functionality",
                    "effort": "quick",
                    "dependencies": [],
                }
            )

        # Authentication resources
        if any(kw in combined for kw in ["auth", "identity", "credential", "key vault", "secret"]):
            resources.append(
                {
                    "name": "App Registration / Service Principal",
                    "type": "identity",
                    "purpose": "Authenticate against required services",
                    "effort": "moderate",
                    "dependencies": [],
                }
            )

        # Log / monitoring resources
        if "log_analysis" in recommended_agents or any(
            kw in combined for kw in ["log", "monitor", "telemetry", "insight"]
        ):
            resources.append(
                {
                    "name": "Log Workspace (dev tier)",
                    "type": "monitoring",
                    "purpose": "Ingest sample logs for analysis agents to query",
                    "effort": "quick",
                    "dependencies": [],
                }
            )

        # Security scanning resources
        if "security_sentinel" in recommended_agents or any(
            kw in combined for kw in ["scan", "vulnerability", "security"]
        ):
            resources.append(
                {
                    "name": "Security Scanner Config",
                    "type": "security-tool",
                    "purpose": "Provide scanning target/configuration for security agent",
                    "effort": "quick",
                    "dependencies": [],
                }
            )

        # Default: if nothing matched, note that no extra resources needed
        # (the plan may be purely advisory)

        return resources
