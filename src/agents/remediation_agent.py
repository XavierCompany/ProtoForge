"""Remediation Agent — fix suggestions, patches, and auto-remediation.

Keeps prior-context awareness: checks for results from log_analysis and
code_research agents before generating fixes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from src.agents.base import BaseAgent
from src.orchestrator.context import AgentResult, ConversationContext

if TYPE_CHECKING:
    from src.forge.loader import AgentManifest

logger = structlog.get_logger(__name__)

_DEFAULT_REMEDIATION_PROMPT = """You are the Remediation Agent — an expert in diagnosing issues and generating fixes.

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
    """Fix suggestions, code patches, workarounds, and rollback strategies.

    Checks for prior results from ``log_analysis`` and ``code_research``
    agents in the conversation context before generating remediation plans,
    so fixes are informed by earlier diagnostic output.
    """

    def __init__(
        self,
        agent_id: str = "remediation",
        description: str = "Bug fixes, patches, hotfixes, workarounds, and resolution steps",
        system_prompt: str = _DEFAULT_REMEDIATION_PROMPT,
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
        _params: dict[str, Any] | None = None,
    ) -> AgentResult:
        """Generate a remediation plan with code patches and rollback strategy."""
        logger.info("remediation_agent_executing", message_length=len(message))

        # Check if there's prior analysis from log_analysis or code_research
        prior_results = [r for r in context.agent_results if r.agent_id in ("log_analysis", "code_research")]

        # Enrich message with prior diagnostic context for LLM
        enriched = message
        if prior_results:
            for pr in prior_results:
                enriched += f"\n\n[Prior {pr.agent_id} analysis:\n{pr.content[:800]}]"

        messages = self._build_messages(enriched, context)

        # ── Try LLM ────────────────────────────────────────────────────
        llm_response = await self._call_llm(messages)
        if llm_response:
            return AgentResult(
                agent_id=self.agent_id,
                content=llm_response,
                confidence=0.9 if prior_results else 0.8,
                artifacts={"prior_context_used": len(prior_results), "source": "llm"},
            )

        # ── Fallback (no LLM configured) ───────────────────────────────
        response = f"**Remediation Plan**\n\n**Issue:** {message[:100]}{'...' if len(message) > 100 else ''}\n"

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
