"""MCP protocol helpers — request/response formatting for the MCP standard."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MCPMessageType(str, Enum):
    """MCP message types per the protocol specification."""

    INITIALIZE = "initialize"
    TOOLS_LIST = "tools/list"
    TOOLS_CALL = "tools/call"
    RESOURCES_LIST = "resources/list"
    RESOURCES_READ = "resources/read"
    PROMPTS_LIST = "prompts/list"
    PROMPTS_GET = "prompts/get"


@dataclass
class MCPToolDefinition:
    """A tool exposed via MCP — maps to a skill in our system."""

    name: str
    description: str
    input_schema: dict[str, Any]
    agent_type: str | None = None  # Which agent handles this tool


@dataclass
class MCPResourceDefinition:
    """A resource exposed via MCP."""

    uri: str
    name: str
    description: str
    mime_type: str = "text/plain"


@dataclass
class MCPPromptDefinition:
    """A prompt template exposed via MCP."""

    name: str
    description: str
    arguments: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class MCPRequest:
    """Incoming MCP request."""

    method: str
    params: dict[str, Any] = field(default_factory=dict)
    id: str | int | None = None


@dataclass
class MCPResponse:
    """Outgoing MCP response."""

    result: Any = None
    error: dict[str, Any] | None = None
    id: str | int | None = None

    def to_dict(self) -> dict[str, Any]:
        resp: dict[str, Any] = {"jsonrpc": "2.0", "id": self.id}
        if self.error:
            resp["error"] = self.error
        else:
            resp["result"] = self.result
        return resp
