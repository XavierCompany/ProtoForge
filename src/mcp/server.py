"""MCP Server — exposes ProtoForge agents as MCP tools for any AI client.

This server implements the Model Context Protocol (MCP) standard,
making ProtoForge skills available to Claude, Codex, Gemini, GPT,
or any MCP-compatible client.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from src.mcp.protocol import (
    MCPMessageType,
    MCPPromptDefinition,
    MCPRequest,
    MCPResourceDefinition,
    MCPResponse,
    MCPToolDefinition,
)
from src.mcp.skills import Skill, SkillLoader

if TYPE_CHECKING:
    from pathlib import Path

logger = structlog.get_logger(__name__)

SERVER_INFO = {
    "name": "protoforge",
    "version": "0.1.1",
    "description": "Multi-agent orchestrator with specialized skills",
}

CAPABILITIES = {
    "tools": {"listChanged": True},
    "resources": {"subscribe": False, "listChanged": True},
    "prompts": {"listChanged": True},
}


class MCPSkillServer:
    """MCP server that distributes ProtoForge agent skills to any AI client.

    Platform-agnostic: works with Claude (Opus), OpenAI (Codex/GPT),
    Google (Gemini), or any MCP-compatible consumer.

    Supports:
    - tools/list → returns all registered skills as MCP tools
    - tools/call → routes tool calls to the appropriate agent
    - resources/list → lists available knowledge resources
    - prompts/list → lists available prompt templates
    """

    def __init__(self, skills_dir: Path | None = None) -> None:
        self._skills: dict[str, Skill] = {}
        self._tools: dict[str, MCPToolDefinition] = {}
        self._resources: dict[str, MCPResourceDefinition] = {}
        self._prompts: dict[str, MCPPromptDefinition] = {}
        self._call_handler: Any = None  # Orchestrator callback

        if skills_dir:
            self.load_skills(skills_dir)

    def load_skills(self, directory: Path) -> None:
        """Load skills from YAML files and register as MCP tools."""
        skills = SkillLoader.load_from_directory(directory)
        for skill in skills:
            self._skills[skill.name] = skill
            self._tools[skill.name] = skill.to_mcp_tool()
        logger.info("mcp_skills_loaded", count=len(skills))

    def register_tool(self, tool: MCPToolDefinition) -> None:
        """Register an additional MCP tool manually."""
        self._tools[tool.name] = tool

    def register_resource(self, resource: MCPResourceDefinition) -> None:
        """Register an MCP resource."""
        self._resources[resource.uri] = resource

    def register_prompt(self, prompt: MCPPromptDefinition) -> None:
        """Register an MCP prompt template."""
        self._prompts[prompt.name] = prompt

    def set_call_handler(self, handler: Any) -> None:
        """Set the handler for tools/call — typically the orchestrator engine."""
        self._call_handler = handler

    async def handle_request(self, request: MCPRequest) -> MCPResponse:
        """Handle an incoming MCP request and return the appropriate response."""
        method = request.method

        handlers = {
            MCPMessageType.INITIALIZE.value: self._handle_initialize,
            MCPMessageType.TOOLS_LIST.value: self._handle_tools_list,
            MCPMessageType.TOOLS_CALL.value: self._handle_tools_call,
            MCPMessageType.RESOURCES_LIST.value: self._handle_resources_list,
            MCPMessageType.RESOURCES_READ.value: self._handle_resources_read,
            MCPMessageType.PROMPTS_LIST.value: self._handle_prompts_list,
            MCPMessageType.PROMPTS_GET.value: self._handle_prompts_get,
        }

        handler = handlers.get(method)
        if not handler:
            return MCPResponse(
                error={"code": -32601, "message": f"Method not found: {method}"},
                id=request.id,
            )

        try:
            result = await handler(request.params)
            return MCPResponse(result=result, id=request.id)
        except Exception as exc:
            logger.error("mcp_request_failed", method=method, error=str(exc))
            return MCPResponse(
                error={"code": -32603, "message": str(exc)},
                id=request.id,
            )

    async def _handle_initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle initialize handshake."""
        logger.info("mcp_initialize", client=params.get("clientInfo", {}))
        return {
            "protocolVersion": "2024-11-05",
            "serverInfo": SERVER_INFO,
            "capabilities": CAPABILITIES,
        }

    async def _handle_tools_list(self, _params: dict[str, Any]) -> dict[str, Any]:
        """Return all registered tools/skills."""
        tools = [
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.input_schema,
            }
            for tool in self._tools.values()
        ]
        return {"tools": tools}

    async def _handle_tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
        """Route a tool call to the appropriate agent via the orchestrator."""
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        logger.info("mcp_tool_call", tool=tool_name, args=list(arguments.keys()))

        if not self._call_handler:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Tool '{tool_name}' registered but no handler configured.",
                    }
                ],
                "isError": True,
            }

        # Route through the orchestrator
        tool_def = self._tools.get(tool_name)
        agent_type = tool_def.agent_type if tool_def else None

        result = await self._call_handler(tool_name, arguments, agent_type)

        return {
            "content": [{"type": "text", "text": str(result)}],
            "isError": False,
        }

    async def _handle_resources_list(self, _params: dict[str, Any]) -> dict[str, Any]:
        """Return all registered resources."""
        resources = [
            {
                "uri": res.uri,
                "name": res.name,
                "description": res.description,
                "mimeType": res.mime_type,
            }
            for res in self._resources.values()
        ]
        return {"resources": resources}

    async def _handle_resources_read(self, params: dict[str, Any]) -> dict[str, Any]:
        """Read a specific resource."""
        uri = params.get("uri", "")
        resource = self._resources.get(uri)
        if not resource:
            return {"contents": []}
        # TODO: Actually read the resource content
        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": resource.mime_type,
                    "text": f"Resource content for {uri}",
                }
            ]
        }

    async def _handle_prompts_list(self, _params: dict[str, Any]) -> dict[str, Any]:
        """Return all registered prompt templates."""
        prompts = [
            {
                "name": p.name,
                "description": p.description,
                "arguments": p.arguments,
            }
            for p in self._prompts.values()
        ]
        return {"prompts": prompts}

    async def _handle_prompts_get(self, params: dict[str, Any]) -> dict[str, Any]:
        """Get a specific prompt template."""
        name = params.get("name", "")
        prompt = self._prompts.get(name)
        if not prompt:
            return {"description": "", "messages": []}
        return {
            "description": prompt.description,
            "messages": [{"role": "user", "content": {"type": "text", "text": prompt.description}}],
        }

    def get_status(self) -> dict[str, Any]:
        """Return MCP server status."""
        return {
            "tools_count": len(self._tools),
            "resources_count": len(self._resources),
            "prompts_count": len(self._prompts),
            "skills_loaded": list(self._skills.keys()),
        }
