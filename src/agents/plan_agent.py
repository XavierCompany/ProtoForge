"""Plan Agent — task decomposition, strategy, and architecture planning."""

from __future__ import annotations

from typing import Any

import structlog

from src.agents.base import BaseAgent
from src.orchestrator.context import AgentResult, ConversationContext

logger = structlog.get_logger(__name__)

PLAN_SYSTEM_PROMPT = """You are the Plan Agent — an expert in task decomposition, strategic planning, and architecture design.

Your responsibilities:
1. Break complex requests into actionable, ordered steps
2. Identify dependencies between tasks
3. Estimate effort and complexity for each step
4. Propose architecture decisions with trade-offs
5. Create milestone-based plans with clear deliverables

Output format:
- Start with a brief summary of the approach
- List numbered steps with effort estimates
- Flag any risks or dependencies
- End with success criteria

Always be specific and actionable. Avoid vague recommendations."""


class PlanAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__(
            agent_id="plan_agent",
            description="Task planning, decomposition, strategy, and architecture decisions",
            system_prompt=PLAN_SYSTEM_PROMPT,
        )

    async def execute(
        self,
        message: str,
        context: ConversationContext,
        params: dict[str, Any] | None = None,
    ) -> AgentResult:
        logger.info("plan_agent_executing", message_length=len(message))

        messages = self._build_messages(message, context)

        # TODO: Wire to Semantic Kernel / LLM call
        # For now, return a structured placeholder showing the agent's capability
        plan_response = (
            f"**Plan Agent Analysis**\n\n"
            f"I've analyzed your request and here's my proposed approach:\n\n"
            f"1. **Understand Requirements** — Parse the full scope of '{message[:80]}...'\n"
            f"2. **Identify Components** — Map affected systems and dependencies\n"
            f"3. **Design Solution** — Propose architecture with trade-offs\n"
            f"4. **Implementation Steps** — Break into atomic, testable tasks\n"
            f"5. **Validation** — Define success criteria and test plan\n\n"
            f"_Ready to proceed with detailed planning once LLM is connected._"
        )

        return AgentResult(
            agent_id=self.agent_id,
            content=plan_response,
            confidence=0.85,
            artifacts={"step_count": 5, "has_dependencies": True},
        )
