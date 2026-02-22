"""Base agent interface — all subagents inherit from this."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from src.orchestrator.context import AgentResult, ConversationContext


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
