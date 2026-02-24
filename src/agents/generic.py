"""Generic Agent — manifest-driven agent with no custom logic.

This replaces the copy-paste boilerplate pattern where every agent
file defined a constant system prompt and a near-identical ``execute()``
method.  Any agent that doesn't need specialised Python logic can be
instantiated as a ``GenericAgent`` directly from its forge manifest.

Agents *with* unique runtime behaviour (e.g.  pattern-detection regex,
prior-context queries) should still subclass ``BaseAgent`` and override
``execute()`` — but they should also accept a manifest via
``BaseAgent.from_manifest()`` so their prompts still come from forge/.
"""

from __future__ import annotations

from typing import Any

import structlog

from src.agents.base import BaseAgent
from src.orchestrator.context import AgentResult, ConversationContext

logger = structlog.get_logger(__name__)


class GenericAgent(BaseAgent):
    """Concrete agent whose entire behaviour comes from the forge manifest.

    Creation:
        >>> agent = GenericAgent.from_manifest(manifest)   # preferred
        >>> agent = GenericAgent(agent_id=..., ...)         # explicit

    The ``execute()`` implementation builds the LLM message list, logs the
    call, and returns a placeholder result.  Once an LLM backend is wired in,
    this is the *only* class that needs to be updated — every manifest-driven
    agent benefits automatically.
    """

    async def execute(
        self,
        message: str,
        context: ConversationContext,
        _params: dict[str, Any] | None = None,
    ) -> AgentResult:
        """Build LLM messages from manifest and return agent result.

        Sends the message to the configured LLM backend (Azure AI Foundry
        via DefaultAzureCredential).  Falls back to a placeholder when no
        LLM is configured, keeping tests and offline usage working.
        """
        logger.info(
            "generic_agent_executing",
            agent_id=self.agent_id,
            message_length=len(message),
        )

        messages = self._build_messages(message, context)

        # ── Try LLM ────────────────────────────────────────────────────
        llm_response = await self._call_llm(messages)
        if llm_response:
            return AgentResult(
                agent_id=self.agent_id,
                content=llm_response,
                confidence=0.85,
                artifacts={"source": "llm"},
            )

        # ── Fallback (no LLM configured) ───────────────────────────────
        response = (
            f"**{self.description}**\n\n"
            f"Query: {message[:120]}{'…' if len(message) > 120 else ''}\n\n"
            f"_Agent `{self.agent_id}` ready — connect an LLM backend for full capabilities._"
        )

        return AgentResult(
            agent_id=self.agent_id,
            content=response,
            confidence=0.6,
            artifacts={"source": "generic_agent"},
        )
