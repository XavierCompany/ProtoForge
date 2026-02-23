"""Base agent interface — all subagents inherit from this."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from src.orchestrator.context import AgentResult, ConversationContext

logger = structlog.get_logger(__name__)


class BaseAgent(ABC):
    """Abstract base class for all subagents in the orchestrator.

    Each agent:
    - Has a unique agent_id and description
    - Has a system prompt tailored to its specialty
    - Implements execute() to process messages
    - Can access shared conversation context
    - Returns structured AgentResult
    """

    def __init__(self, agent_id: str, description: str, system_prompt: str) -> None:
        self._agent_id = agent_id
        self._description = description
        self._system_prompt = system_prompt

    @property
    def agent_id(self) -> str:
        return self._agent_id

    @property
    def description(self) -> str:
        return self._description

    @property
    def system_prompt(self) -> str:
        return self._system_prompt

    @abstractmethod
    async def execute(
        self,
        message: str,
        context: ConversationContext,
        params: dict[str, Any] | None = None,
    ) -> AgentResult:
        """Process a user message and return a structured result.

        Args:
            message: The user's input message.
            context: Shared conversation context with history and memory.
            params: Optional extracted parameters from intent routing.

        Returns:
            AgentResult with the agent's response and metadata.
        """
        ...

    def _build_messages(
        self, message: str, context: ConversationContext
    ) -> list[dict[str, str]]:
        """Build the message list for LLM consumption, including system prompt."""
        messages = [{"role": "system", "content": self._system_prompt}]
        messages.extend(context.get_history_for_agent(last_n=10))
        messages.append({"role": "user", "content": message})
        return messages

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} id={self._agent_id}>"

    async def _call_llm(self, messages: list[dict[str, str]]) -> str | None:
        """Call the configured LLM provider and return the response text.

        Returns None when no credentials are configured so callers can fall
        back to their built-in stub behaviour gracefully.
        """
        from src.config import LLMProvider, get_settings

        settings = get_settings()
        provider = settings.llm.active_provider

        try:
            if provider == LLMProvider.OPENAI and settings.llm.openai_api_key:
                from openai import AsyncOpenAI

                client = AsyncOpenAI(api_key=settings.llm.openai_api_key)
                response = await client.chat.completions.create(
                    model=settings.llm.openai_model,
                    messages=messages,  # type: ignore[arg-type]
                )
                return response.choices[0].message.content

            if provider == LLMProvider.AZURE_AI_FOUNDRY and settings.llm.azure_endpoint:
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
                    messages=messages,  # type: ignore[arg-type]
                )
                return response.choices[0].message.content

        except Exception as exc:
            logger.warning("llm_call_failed", agent_id=self._agent_id, error=str(exc))

        # No credentials configured or call failed — caller falls back to stub
        return None
