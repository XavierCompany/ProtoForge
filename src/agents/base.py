"""Base agent interface — all subagents inherit from this.

Agents can be constructed in two ways:

1. **Manifest-driven** (preferred) — ``BaseAgent.from_manifest(manifest)``
   reads agent_id, description, and system_prompt from a forge
   ``AgentManifest``, ensuring the declarative ``forge/`` YAML is the single
   source of truth.

2. **Explicit arguments** — the classic ``__init__(agent_id, description,
   system_prompt)`` still works for tests and agents that don't yet have a
   forge manifest.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.forge.loader import AgentManifest
    from src.orchestrator.context import AgentResult, ConversationContext


class BaseAgent(ABC):
    """Abstract base class for all subagents in the orchestrator.

    Each agent:
    - Has a unique agent_id and description
    - Has a system prompt tailored to its specialty
    - Implements execute() to process messages
    - Can access shared conversation context
    - Returns structured AgentResult
    - Optionally holds a reference to its forge AgentManifest
    """

    def __init__(
        self,
        agent_id: str,
        description: str,
        system_prompt: str,
        *,
        manifest: AgentManifest | None = None,
    ) -> None:
        self._agent_id = agent_id
        self._description = description
        self._system_prompt = system_prompt
        self._manifest = manifest

    # -- Factory -----------------------------------------------------------

    @classmethod
    def from_manifest(cls, manifest: AgentManifest, **kwargs: Any) -> BaseAgent:
        """Create an agent from a forge ``AgentManifest``.

        The system prompt is read from the manifest's resolved_prompts
        (the ``system`` key).  Falls back to the manifest description when
        no prompt file was found.

        Sub-classes can override this to inject additional dependencies.
        """
        system_prompt = manifest.resolved_prompts.get("system", "")
        if not system_prompt:
            system_prompt = f"You are {manifest.name}.\n\n{manifest.description}"
        return cls(
            agent_id=manifest.id,
            description=manifest.description,
            system_prompt=system_prompt,
            manifest=manifest,
            **kwargs,
        )

    # -- Properties --------------------------------------------------------

    @property
    def agent_id(self) -> str:
        return self._agent_id

    @property
    def description(self) -> str:
        return self._description

    @property
    def system_prompt(self) -> str:
        return self._system_prompt

    @property
    def manifest(self) -> AgentManifest | None:
        return self._manifest

    # -- Abstract ----------------------------------------------------------

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

    # -- Helpers -----------------------------------------------------------

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
