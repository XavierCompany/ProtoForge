"""Tests for the agent catalog and workflow engine."""

import pytest

from src.mcp.skills import Skill
from src.registry.catalog import AgentCatalog, AgentRegistration, CatalogEntry
from src.registry.workflows import Workflow, WorkflowEngine, WorkflowStep


class TestAgentCatalog:
    def test_register_and_get_agent(self) -> None:
        catalog = AgentCatalog()
        reg = AgentRegistration(
            agent_type="plan",
            name="Plan Agent",
            description="Planning agent",
        )
        catalog.register_agent(reg)
        assert catalog.get_agent("plan") is not None
        assert catalog.get_agent("plan").name == "Plan Agent"

    def test_list_agents_with_filter(self) -> None:
        catalog = AgentCatalog()
        catalog.register_agent(
            AgentRegistration(
                agent_type="plan",
                name="Plan",
                description="",
                status="active",
                tags=["planning"],
            )
        )
        catalog.register_agent(
            AgentRegistration(
                agent_type="log",
                name="Log",
                description="",
                status="disabled",
            )
        )

        active = catalog.list_agents(status="active")
        assert len(active) == 1
        assert active[0].agent_type == "plan"

        tagged = catalog.list_agents(tag="planning")
        assert len(tagged) == 1

    def test_update_metrics(self) -> None:
        catalog = AgentCatalog()
        catalog.register_agent(
            AgentRegistration(
                agent_type="plan",
                name="Plan",
                description="",
            )
        )
        catalog.update_agent_metrics("plan", latency_ms=100.0)
        catalog.update_agent_metrics("plan", latency_ms=200.0)

        agent = catalog.get_agent("plan")
        assert agent.usage_count == 2
        assert agent.avg_latency_ms == pytest.approx(150.0)

    def test_search_catalog(self) -> None:
        catalog = AgentCatalog()
        catalog.add_to_catalog(
            CatalogEntry(
                skill_name="plan_task",
                description="Planning",
                agent_type="plan",
                version="1.0.0",
                tags=["planning"],
                installed=True,
            )
        )
        catalog.add_to_catalog(
            CatalogEntry(
                skill_name="security_scan",
                description="Security",
                agent_type="security",
                version="1.0.0",
                tags=["security"],
            )
        )

        results = catalog.search_catalog(query="plan")
        assert len(results) == 1

        results = catalog.search_catalog(installed_only=True)
        assert len(results) == 1

    def test_install_uninstall_skill(self) -> None:
        catalog = AgentCatalog()
        catalog.add_to_catalog(
            CatalogEntry(
                skill_name="test",
                description="",
                agent_type="plan",
                version="1.0.0",
            )
        )
        assert not catalog.search_catalog(installed_only=True)

        catalog.install_skill("test")
        assert len(catalog.search_catalog(installed_only=True)) == 1

        catalog.uninstall_skill("test")
        assert not catalog.search_catalog(installed_only=True)

    def test_populate_from_skills(self) -> None:
        catalog = AgentCatalog()
        skills = [
            Skill(name="s1", description="d1", agent_type="plan", tags=["t1"]),
            Skill(name="s2", description="d2", agent_type="log", tags=["t2"]),
        ]
        catalog.populate_from_skills(skills)
        assert len(catalog.search_catalog()) == 2

    def test_status(self) -> None:
        catalog = AgentCatalog()
        status = catalog.get_status()
        assert status["total_agents"] == 0
        assert status["total_skills"] == 0


class TestWorkflow:
    def test_execution_order_linear(self) -> None:
        wf = Workflow(
            name="test",
            description="test",
            steps=[
                WorkflowStep(name="a", agent_type="plan", prompt_template=""),
                WorkflowStep(name="b", agent_type="log", prompt_template="", depends_on=["a"]),
                WorkflowStep(name="c", agent_type="fix", prompt_template="", depends_on=["b"]),
            ],
        )
        waves = wf.get_execution_order()
        assert len(waves) == 3
        assert waves[0][0].name == "a"
        assert waves[1][0].name == "b"
        assert waves[2][0].name == "c"

    def test_execution_order_parallel(self) -> None:
        wf = Workflow(
            name="test",
            description="test",
            steps=[
                WorkflowStep(name="a", agent_type="plan", prompt_template=""),
                WorkflowStep(name="b", agent_type="log", prompt_template=""),
                WorkflowStep(name="c", agent_type="fix", prompt_template="", depends_on=["a", "b"]),
            ],
        )
        waves = wf.get_execution_order()
        assert len(waves) == 2
        # First wave has a and b in parallel
        first_wave_names = {s.name for s in waves[0]}
        assert first_wave_names == {"a", "b"}
        assert waves[1][0].name == "c"


class TestWorkflowEngine:
    @pytest.mark.asyncio
    async def test_execute_targets_declared_agent_type(self) -> None:
        from src.orchestrator.context import AgentResult, ConversationContext

        class FakeOrchestrator:
            def __init__(self) -> None:
                self.context = ConversationContext()
                self._router = object()
                self.dispatch_calls: list[tuple[str, str, str]] = []
                self.process_calls: list[str] = []

            async def _dispatch(self, agent_id, message, routing, _ctx):
                self.dispatch_calls.append((agent_id, message, routing.primary_agent))
                return AgentResult(agent_id=agent_id, content=f"{agent_id}:{message}", confidence=1.0)

            async def process(self, prompt: str):
                self.process_calls.append(prompt)
                return f"process:{prompt}", self.context

        orchestrator = FakeOrchestrator()
        engine = WorkflowEngine(orchestrator)
        engine.register_workflow(
            Workflow(
                name="wf",
                description="test",
                steps=[
                    WorkflowStep(
                        name="scan",
                        agent_type="security_sentinel",
                        prompt_template="scan {target}",
                        params={"severity": "high"},
                    )
                ],
            )
        )

        result = await engine.execute("wf", initial_params={"target": "repo"})
        assert result["steps_completed"] == 1
        assert orchestrator.dispatch_calls == [("security_sentinel", "scan repo", "security_sentinel")]
        assert orchestrator.process_calls == []
        assert result["results"]["scan"]["agent_type"] == "security_sentinel"
        assert result["results"]["scan"]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_execute_falls_back_to_process_when_dispatch_unavailable(self) -> None:
        class ProcessOnlyOrchestrator:
            def __init__(self) -> None:
                self.context = type("Ctx", (), {"active_workflow": None})()
                self.process_calls: list[str] = []

            async def process(self, prompt: str):
                self.process_calls.append(prompt)
                return f"process:{prompt}", self.context

        orchestrator = ProcessOnlyOrchestrator()
        engine = WorkflowEngine(orchestrator)
        engine.register_workflow(
            Workflow(
                name="wf-process",
                description="test",
                steps=[WorkflowStep(name="step1", agent_type="plan", prompt_template="plan {topic}")],
            )
        )

        result = await engine.execute("wf-process", initial_params={"topic": "release"})
        assert result["steps_completed"] == 1
        assert orchestrator.process_calls == ["plan release"]
        assert result["results"]["step1"]["content"] == "process:plan release"
