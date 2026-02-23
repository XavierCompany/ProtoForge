"""Remediation Agent — fix suggestions, patches, and auto-remediation."""

from __future__ import annotations

from typing import Any

import structlog

from src.agents.base import BaseAgent
from src.orchestrator.context import AgentResult, ConversationContext

logger = structlog.get_logger(__name__)

REMEDIATION_SYSTEM_PROMPT = """You are the Remediation Agent — an expert in diagnosing issues and generating fixes.

Your responsibilities:
1. Analyze errors and propose concrete fixes
2. Generate code patches (unified diff format)
3. Suggest workarounds when direct fixes aren't possible
4. Validate that proposed fixes don't introduce regressions
5. Provide rollback strategies

Output format:
- Issue summary
- Root cause (if determinable)
- Proposed fix with code changes
- Risk assessment of the fix
- Testing recommendations
- Rollback plan

Always provide code diffs. Explain WHY the fix works. Consider edge cases."""


class RemediationAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__(
            agent_id="remediation_agent",
            description="Bug fixes, patches, hotfixes, workarounds, and resolution steps",
            system_prompt=REMEDIATION_SYSTEM_PROMPT,
        )

    async def execute(
        self,
        message: str,
        context: ConversationContext,
        params: dict[str, Any] | None = None,
    ) -> AgentResult:
        logger.info("remediation_agent_executing", message_length=len(message))

        # Check if there's prior analysis from log_analysis or code_research
        prior_results = [
            r for r in context.agent_results
            if r.agent_id in ("log_analysis_agent", "code_research_agent")
        ]

        messages = self._build_messages(message, context)
        llm_response = await self._call_llm(messages)
        if llm_response:
            response = llm_response
        else:
            response = (
                f"**Remediation Plan**\n\n"
                f"**Issue:** {message[:100]}{'...' if len(message) > 100 else ''}\n"
            )

            if prior_results:
                response += f"\n**Building on:** {len(prior_results)} prior analysis result(s)\n"

            response += (
                "\n**Proposed Actions:**\n"
                "1. Identify the exact failure point\n"
                "2. Generate targeted code patch\n"
                "3. Validate fix doesn't break existing tests\n"
                "4. Provide rollback procedure\n\n"
                "_Connect LLM backend for actual patch generation._"
            )

        return AgentResult(
            agent_id=self.agent_id,
            content=response,
            confidence=0.65,
            artifacts={"prior_context_used": len(prior_results)},
        )
