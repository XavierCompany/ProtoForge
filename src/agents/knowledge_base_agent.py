"""Knowledge Base Agent — documentation, RAG retrieval, how-to guides."""

from __future__ import annotations

from typing import Any

import structlog

from src.agents.base import BaseAgent
from src.orchestrator.context import AgentResult, ConversationContext

logger = structlog.get_logger(__name__)

KB_SYSTEM_PROMPT = """You are the Knowledge Base Agent — an expert in documentation retrieval and knowledge management.

Your responsibilities:
1. Search internal documentation, wikis, and knowledge bases
2. Retrieve relevant how-to guides and tutorials
3. Explain technical concepts clearly
4. Provide contextual documentation references
5. Synthesize information from multiple sources

Output format:
- Direct answer to the question
- Relevant documentation excerpts
- Links to source documents
- Related topics for further reading
- Confidence level in the answer

Be comprehensive but concise. Cite sources. Distinguish between facts and inferences."""


class KnowledgeBaseAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__(
            agent_id="knowledge_base_agent",
            description="Documentation retrieval, how-to guides, explanations, and knowledge queries",
            system_prompt=KB_SYSTEM_PROMPT,
        )
        self._knowledge_sources: list[str] = []

    def add_knowledge_source(self, source_path: str) -> None:
        """Register an additional knowledge source (file, URL, or index)."""
        self._knowledge_sources.append(source_path)
        logger.info("knowledge_source_added", source=source_path)

    async def execute(
        self,
        message: str,
        context: ConversationContext,
        params: dict[str, Any] | None = None,
    ) -> AgentResult:
        logger.info(
            "knowledge_base_agent_executing",
            message_length=len(message),
            sources=len(self._knowledge_sources),
        )

        messages = self._build_messages(message, context)
        llm_response = await self._call_llm(messages)
        if llm_response:
            response = llm_response
        else:
            response = (
                f"**Knowledge Base Response**\n\n"
                f"Query: {message[:100]}{'...' if len(message) > 100 else ''}\n\n"
                f"**Knowledge Sources:** {len(self._knowledge_sources)} registered\n\n"
                f"**Search Strategy:**\n"
                f"1. Semantic search across indexed documentation\n"
                f"2. Keyword matching in registered knowledge bases\n"
                f"3. RAG retrieval with context windowing\n\n"
                f"_Connect LLM backend + vector store for full RAG capabilities._"
            )

        return AgentResult(
            agent_id=self.agent_id,
            content=response,
            confidence=0.5,
            artifacts={"sources_searched": len(self._knowledge_sources)},
        )
