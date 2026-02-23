"""Plan Agent — top-level coordinator for all orchestrated requests.

The Plan Agent is always invoked first for every user message. It:
1. Analyzes the request and produces a strategic plan
2. Identifies which sub-agents should be invoked
3. Provides structured context that downstream sub-agents can reference
"""

from __future__ import annotations

from typing import Any

import structlog

from src.agents.base import BaseAgent
from src.orchestrator.context import AgentResult, ConversationContext

logger = structlog.get_logger(__name__)

PLAN_SYSTEM_PROMPT = """You are the Plan Agent — the top-level coordinator in a multi-agent system.

You are ALWAYS invoked FIRST for every user request. Your job is to:

1. **Analyze** the user's request and understand the full scope
2. **Decompose** complex requests into actionable, ordered steps
3. **Route** — Identify which specialist sub-agents should be invoked:
   - log_analysis: Log parsing, error analysis, stack traces, crash investigation
   - code_research: Code search, function lookup, implementation understanding
   - remediation: Bug fixes, patches, hotfixes, workarounds
   - knowledge_base: Documentation, how-to guides, explanations, knowledge retrieval
   - data_analysis: Data analysis, metrics, charts, trends, statistical analysis
   - security_sentinel: Security scanning, vulnerability assessment, CVE lookup, audits
4. **Plan** — Create milestone-based plan with dependencies and success criteria
5. **Coordinate** — Provide structured context for downstream sub-agents

Output format:
- Brief summary of the approach
- Numbered steps with effort estimates
- List of recommended sub-agents to invoke and why
- Risks or dependencies to watch
- Success criteria

Always be specific and actionable. Avoid vague recommendations."""

# Sub-agent types the Plan Agent can recommend (everything except itself)
SUB_AGENT_TYPES = [
    "log_analysis", "code_research", "remediation",
    "knowledge_base", "data_analysis", "security_sentinel",
]


class PlanAgent(BaseAgent):
    """Top-level planning agent — always runs first in the orchestration pipeline."""

    def __init__(self) -> None:
        super().__init__(
            agent_id="plan_agent",
            description="Top-level coordinator: analyzes requests, produces plans, and identifies sub-agents",
            system_prompt=PLAN_SYSTEM_PROMPT,
        )

    async def execute(
        self,
        message: str,
        context: ConversationContext,
        params: dict[str, Any] | None = None,
    ) -> AgentResult:
        logger.info("plan_agent_executing", message_length=len(message), role="top_level")

        messages = self._build_messages(message, context)

        # Determine which sub-agents are relevant based on routing params
        recommended_agents = self._identify_sub_agents(message, params)
        agent_list = ", ".join(recommended_agents) if recommended_agents else "knowledge_base"

        llm_response = await self._call_llm(messages)
        if llm_response:
            plan_response = llm_response
        else:
            # Fallback stub when no LLM is configured
            plan_response = (
                f"**Plan Agent — Coordination Plan**\n\n"
                f"I've analyzed your request and here's the execution strategy:\n\n"
                f"1. **Understand Requirements** — Parse the full scope of '{message[:80]}...'\n"
                f"2. **Identify Components** — Map affected systems and dependencies\n"
                f"3. **Design Solution** — Propose architecture with trade-offs\n"
                f"4. **Delegate to Sub-Agents** — Invoke [{agent_list}] for specialized work\n"
                f"5. **Validation** — Define success criteria and test plan\n\n"
                f"**Recommended sub-agents:** {agent_list}\n\n"
                f"_Proceeding to dispatch sub-agents for execution._"
            )

        return AgentResult(
            agent_id=self.agent_id,
            content=plan_response,
            confidence=0.85,
            artifacts={
                "step_count": 5,
                "has_dependencies": True,
                "recommended_sub_agents": recommended_agents,
            },
        )

    def _identify_sub_agents(self, message: str, params: dict[str, Any] | None) -> list[str]:
        """Identify which sub-agents the plan recommends invoking."""
        agents: list[str] = []
        msg_lower = message.lower()

        # Simple keyword-based identification (will be replaced by LLM reasoning)
        if any(kw in msg_lower for kw in ["log", "error", "trace", "crash", "exception"]):
            agents.append("log_analysis")
        if any(kw in msg_lower for kw in ["code", "function", "class", "implement", "source"]):
            agents.append("code_research")
        if any(kw in msg_lower for kw in ["fix", "patch", "resolve", "repair", "hotfix", "remediat"]):
            agents.append("remediation")
        if any(kw in msg_lower for kw in ["doc", "how to", "explain", "what is", "knowledge"]):
            agents.append("knowledge_base")
        if any(kw in msg_lower for kw in ["data", "metric", "chart", "trend", "statistic", "analyze"]):
            agents.append("data_analysis")
        if any(kw in msg_lower for kw in ["security", "vulnerab", "cve", "scan", "audit", "threat"]):
            agents.append("security_sentinel")

        # Default to knowledge_base if nothing matched
        if not agents:
            agents.append("knowledge_base")

        return agents
