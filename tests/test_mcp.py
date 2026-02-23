"""Tests for the MCP server."""

import pytest

from src.mcp.protocol import MCPRequest
from src.mcp.server import MCPSkillServer
from src.mcp.skills import Skill, SkillParameter


@pytest.fixture
def mcp_server() -> MCPSkillServer:
    return MCPSkillServer()


@pytest.fixture
def sample_skill() -> Skill:
    return Skill(
        name="test_skill",
        description="A test skill",
        agent_type="plan",
        parameters=[
            SkillParameter(name="input", type="string", description="Test input"),
        ],
        tags=["test"],
    )


class TestMCPServer:
    @pytest.mark.asyncio
    async def test_initialize(self, mcp_server: MCPSkillServer) -> None:
        req = MCPRequest(method="initialize", params={"clientInfo": {"name": "test"}}, id=1)
        resp = await mcp_server.handle_request(req)
        assert resp.result is not None
        assert resp.result["protocolVersion"] == "2024-11-05"
        assert resp.result["serverInfo"]["name"] == "protoforge"

    @pytest.mark.asyncio
    async def test_tools_list_empty(self, mcp_server: MCPSkillServer) -> None:
        req = MCPRequest(method="tools/list", params={}, id=2)
        resp = await mcp_server.handle_request(req)
        assert resp.result["tools"] == []

    @pytest.mark.asyncio
    async def test_tools_list_with_skill(self, mcp_server: MCPSkillServer, sample_skill: Skill) -> None:
        # Register a skill as a tool
        tool = sample_skill.to_mcp_tool()
        mcp_server.register_tool(tool)

        req = MCPRequest(method="tools/list", params={}, id=3)
        resp = await mcp_server.handle_request(req)
        tools = resp.result["tools"]
        assert len(tools) == 1
        assert tools[0]["name"] == "test_skill"

    @pytest.mark.asyncio
    async def test_unknown_method(self, mcp_server: MCPSkillServer) -> None:
        req = MCPRequest(method="unknown/method", params={}, id=4)
        resp = await mcp_server.handle_request(req)
        assert resp.error is not None
        assert resp.error["code"] == -32601

    @pytest.mark.asyncio
    async def test_tools_call_no_handler(self, mcp_server: MCPSkillServer) -> None:
        tool = Skill(name="test_tool", description="test", agent_type="plan").to_mcp_tool()
        mcp_server.register_tool(tool)

        req = MCPRequest(
            method="tools/call",
            params={"name": "test_tool", "arguments": {}},
            id=5,
        )
        resp = await mcp_server.handle_request(req)
        assert resp.result["isError"] is True

    def test_get_status(self, mcp_server: MCPSkillServer) -> None:
        status = mcp_server.get_status()
        assert status["tools_count"] == 0
        assert status["resources_count"] == 0


class TestSkillToMCPTool:
    def test_conversion(self, sample_skill: Skill) -> None:
        tool = sample_skill.to_mcp_tool()
        assert tool.name == "test_skill"
        assert tool.agent_type == "plan"
        assert "input" in tool.input_schema["properties"]
        assert "input" in tool.input_schema["required"]
