"""Code Research Agent — code search, function lookup, and implementation understanding."""

from __future__ import annotations

from typing import Any

import structlog

from src.agents.base import BaseAgent
from src.orchestrator.context import AgentResult, ConversationContext

logger = structlog.get_logger(__name__)

CODE_RESEARCH_SYSTEM_PROMPT = """You are the Code Research Agent — an expert in codebase navigation, code search, and implementation analysis.

Your responsibilities:
1. Search codebases for specific functions, classes, patterns
2. Explain code logic and architecture
3. Trace execution flows across files and modules
4. Identify code dependencies and coupling
5. Find similar patterns and suggest best practices

Output format:
- Direct answer to the code question
- Relevant code snippets with file paths and line numbers
- Explanation of how the code works
- Related functions/classes worth examining
- Architecture implications if relevant

Be precise with file paths and line numbers. Show actual code when possible."""


class CodeResearchAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__(
            agent_id="code_research_agent",
            description="Code search, function lookup, implementation understanding, and architecture analysis",
            system_prompt=CODE_RESEARCH_SYSTEM_PROMPT,
        )

    async def execute(
        self,
        message: str,
        context: ConversationContext,
        params: dict[str, Any] | None = None,
    ) -> AgentResult:
        logger.info("code_research_agent_executing", message_length=len(message))

        messages = self._build_messages(message, context)

        response = (
            f"**Code Research Analysis**\n\n"
            f"Query: {message[:100]}{'...' if len(message) > 100 else ''}\n\n"
            f"**Search Strategy:**\n"
            f"1. Semantic search across workspace\n"
            f"2. AST-based function/class lookup\n"
            f"3. Dependency graph traversal\n"
            f"4. Pattern matching for similar implementations\n\n"
            f"_Connect LLM backend + workspace indexer for full capabilities._"
        )

        return AgentResult(
            agent_id=self.agent_id,
            content=response,
            confidence=0.6,
            artifacts={"search_strategy": "semantic+ast+dependency"},
        )
