"""Integration tests for the FastAPI server endpoints and main.py bootstrap."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.agents.knowledge_base_agent import KnowledgeBaseAgent
from src.agents.plan_agent import PlanAgent
from src.mcp.server import MCPSkillServer
from src.orchestrator.engine import OrchestratorEngine
from src.orchestrator.router import AgentType
from src.registry.catalog import AgentCatalog
from src.registry.workflows import WorkflowEngine
from src.server import create_app

# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def orchestrator() -> OrchestratorEngine:
    engine = OrchestratorEngine()
    engine.register_agent(AgentType.PLAN, PlanAgent())
    engine.register_agent(AgentType.KNOWLEDGE_BASE, KnowledgeBaseAgent())
    return engine


@pytest.fixture
def mcp_server() -> MCPSkillServer:
    return MCPSkillServer()


@pytest.fixture
def catalog() -> AgentCatalog:
    return AgentCatalog()


@pytest.fixture
def workflow_engine(orchestrator: OrchestratorEngine) -> WorkflowEngine:
    return WorkflowEngine(orchestrator)


@pytest.fixture
def client(
    orchestrator: OrchestratorEngine,
    mcp_server: MCPSkillServer,
    catalog: AgentCatalog,
    workflow_engine: WorkflowEngine,
) -> TestClient:
    app = create_app(orchestrator, mcp_server, catalog, workflow_engine)
    return TestClient(app)


# ── /health ───────────────────────────────────────────────────────────


class TestHealthEndpoint:
    def test_health_returns_200(self, client: TestClient) -> None:
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_body_has_status(self, client: TestClient) -> None:
        data = client.get("/health").json()
        assert data["status"] == "healthy"

    def test_health_body_has_orchestrator(self, client: TestClient) -> None:
        data = client.get("/health").json()
        assert "orchestrator" in data
        assert "session_id" in data["orchestrator"]

    def test_health_body_has_mcp_and_catalog(self, client: TestClient) -> None:
        data = client.get("/health").json()
        assert "mcp" in data
        assert "catalog" in data


# ── /chat ─────────────────────────────────────────────────────────────


class TestChatEndpoint:
    def test_chat_returns_200(self, client: TestClient) -> None:
        response = client.post("/chat", json={"message": "hello"})
        assert response.status_code == 200

    def test_chat_returns_response_and_session_id(self, client: TestClient) -> None:
        data = client.post("/chat", json={"message": "explain what you do"}).json()
        assert "response" in data
        assert "session_id" in data
        assert len(data["response"]) > 0

    def test_chat_response_contains_plan(self, client: TestClient) -> None:
        data = client.post("/chat", json={"message": "create a plan"}).json()
        assert "Plan" in data["response"]

    def test_chat_missing_message_returns_422(self, client: TestClient) -> None:
        response = client.post("/chat", json={})
        assert response.status_code == 422


# ── /agents ───────────────────────────────────────────────────────────


class TestAgentsEndpoint:
    def test_agents_returns_200(self, client: TestClient) -> None:
        response = client.get("/agents")
        assert response.status_code == 200

    def test_agents_returns_list(self, client: TestClient) -> None:
        data = client.get("/agents").json()
        assert isinstance(data, list)

    def test_agents_list_has_required_fields(
        self,
        orchestrator: OrchestratorEngine,
        mcp_server: MCPSkillServer,
        workflow_engine: WorkflowEngine,
    ) -> None:
        from src.registry.catalog import AgentCatalog, AgentRegistration

        catalog = AgentCatalog()
        catalog.register_agent(AgentRegistration(
            agent_type="plan", name="Plan Agent", description="Planner",
        ))
        app = create_app(orchestrator, mcp_server, catalog, workflow_engine)
        c = TestClient(app)
        data = c.get("/agents").json()
        assert len(data) == 1
        assert data[0]["agent_type"] == "plan"
        assert "name" in data[0]
        assert "status" in data[0]


# ── /workflows ────────────────────────────────────────────────────────


class TestWorkflowsEndpoint:
    def test_workflows_returns_200(self, client: TestClient) -> None:
        response = client.get("/workflows")
        assert response.status_code == 200

    def test_workflows_returns_list(self, client: TestClient) -> None:
        data = client.get("/workflows").json()
        assert isinstance(data, list)


# ── main.py bootstrap ─────────────────────────────────────────────────


class TestBootstrap:
    def test_bootstrap_returns_five_components(self) -> None:
        from src.main import bootstrap

        result = bootstrap()
        assert len(result) == 5

    def test_bootstrap_app_is_fastapi(self) -> None:
        from fastapi import FastAPI

        from src.main import bootstrap

        app, *_ = bootstrap()
        assert isinstance(app, FastAPI)

    def test_bootstrap_registers_all_agents(self) -> None:
        from src.main import bootstrap

        _, orchestrator, *_ = bootstrap()
        status = orchestrator.get_status()
        # All 7 agent types should be registered
        assert len(status["registered_agents"]) == 7

    def test_bootstrap_creates_mcp_server(self) -> None:
        from src.main import bootstrap

        _, _, mcp_server, *_ = bootstrap()
        mcp_status = mcp_server.get_status()
        assert "tools_count" in mcp_status

    def test_bootstrap_creates_catalog(self) -> None:
        from src.main import bootstrap

        _, _, _, catalog, _ = bootstrap()
        agents = catalog.list_agents()
        assert len(agents) == 7
