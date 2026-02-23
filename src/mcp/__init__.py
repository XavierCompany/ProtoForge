"""MCP package — Model Context Protocol server for skills distribution.

Modules
-------
server      MCPServer — JSON-RPC server exposing tools, resources, and prompts
            to any MCP-compatible AI client (Claude, Codex, Gemini, GPT).
protocol    MCPRequest, MCPResponse, MCPToolDefinition, MCPResourceDefinition,
            MCPPromptDefinition — MCP message types and schema definitions.
skills      SkillLoader — discovers and loads skills from forge/ YAML manifests.
"""

__all__ = [
    "MCPServer",
    "SkillLoader",
]
