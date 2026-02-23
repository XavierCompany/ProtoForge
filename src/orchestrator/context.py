"""Shared conversation context and memory for multi-agent orchestration."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


@dataclass
class Message:
    role: MessageRole
    content: str
    agent_id: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentResult:
    """Result returned by a subagent after processing."""

    agent_id: str
    content: str
    confidence: float = 1.0
    artifacts: dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0


@dataclass
class ConversationContext:
    """Shared context that flows between the orchestrator and subagents.

    Provides conversation history, working memory, and artifact storage
    so agents can build on each other's work.
    """

    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    messages: list[Message] = field(default_factory=list)
    working_memory: dict[str, Any] = field(default_factory=dict)
    agent_results: list[AgentResult] = field(default_factory=list)
    active_workflow: str | None = None

    def add_user_message(self, content: str) -> None:
        self.messages.append(Message(role=MessageRole.USER, content=content))

    def add_agent_message(self, agent_id: str, content: str) -> None:
        self.messages.append(Message(role=MessageRole.ASSISTANT, content=content, agent_id=agent_id))

    def add_result(self, result: AgentResult) -> None:
        self.agent_results.append(result)
        self.add_agent_message(result.agent_id, result.content)

    def get_history_for_agent(self, last_n: int = 20) -> list[dict[str, str]]:
        """Get recent conversation history formatted for LLM consumption."""
        recent = self.messages[-last_n:]
        return [{"role": m.role.value, "content": m.content} for m in recent]

    def set_memory(self, key: str, value: Any) -> None:
        self.working_memory[key] = value

    def get_memory(self, key: str, default: Any = None) -> Any:
        return self.working_memory.get(key, default)
