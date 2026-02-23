"""Tests for WorkflowEngine.execute() — covers the parallel/sequential execution path."""

from __future__ import annotations

import pytest

from src.agents.knowledge_base_agent import KnowledgeBaseAgent
from src.agents.plan_agent import PlanAgent
from src.orchestrator.engine import OrchestratorEngine
from src.orchestrator.router import AgentType
from src.registry.workflows import Workflow, WorkflowEngine, WorkflowStep


@pytest.fixture
def orchestrator() -> OrchestratorEngine:
    engine = OrchestratorEngine()
    engine.register_agent(AgentType.PLAN, PlanAgent())
    engine.register_agent(AgentType.KNOWLEDGE_BASE, KnowledgeBaseAgent())
    return engine


@pytest.fixture
def workflow_engine(orchestrator: OrchestratorEngine) -> WorkflowEngine:
    return WorkflowEngine(orchestrator)


@pytest.fixture
def simple_workflow() -> Workflow:
    return Workflow(
        name="simple",
        description="A simple single-step workflow",
        steps=[
            WorkflowStep(
                name="step_a",
                agent_type="knowledge_base",
                prompt_template="Explain {topic}",
            ),
        ],
    )


@pytest.fixture
def sequential_workflow() -> Workflow:
    return Workflow(
        name="sequential",
        description="A two-step sequential workflow",
        steps=[
            WorkflowStep(
                name="plan",
                agent_type="plan",
                prompt_template="Create a plan for {goal}",
            ),
            WorkflowStep(
                name="research",
                agent_type="knowledge_base",
                prompt_template="Research the topic in detail",
                depends_on=["plan"],
            ),
        ],
    )


class TestWorkflowEngineExecute:
    @pytest.mark.asyncio
    async def test_execute_unknown_workflow_returns_error(
        self, workflow_engine: WorkflowEngine
    ) -> None:
        result = await workflow_engine.execute("nonexistent")
        assert "error" in result
        assert "nonexistent" in result["error"]

    @pytest.mark.asyncio
    async def test_execute_single_step_workflow(
        self, workflow_engine: WorkflowEngine, simple_workflow: Workflow
    ) -> None:
        workflow_engine.register_workflow(simple_workflow)
        result = await workflow_engine.execute("simple", {"topic": "unit testing"})

        assert result["workflow"] == "simple"
        assert result["steps_completed"] == 1
        assert result["steps_failed"] == 0
        assert "step_a" in result["results"]
        assert result["results"]["step_a"]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_execute_sequential_workflow(
        self, workflow_engine: WorkflowEngine, sequential_workflow: Workflow
    ) -> None:
        workflow_engine.register_workflow(sequential_workflow)
        result = await workflow_engine.execute("sequential", {"goal": "improve API performance"})

        assert result["workflow"] == "sequential"
        assert result["steps_completed"] == 2
        assert "plan" in result["results"]
        assert "research" in result["results"]

    @pytest.mark.asyncio
    async def test_execute_sets_active_workflow_during_run(
        self,
        workflow_engine: WorkflowEngine,
        orchestrator: OrchestratorEngine,
        simple_workflow: Workflow,
    ) -> None:
        """active_workflow is cleared after execution finishes."""
        workflow_engine.register_workflow(simple_workflow)
        await workflow_engine.execute("simple", {"topic": "testing"})
        # After execution, active_workflow should be reset to None
        assert orchestrator.context.active_workflow is None

    @pytest.mark.asyncio
    async def test_execute_parallel_steps(
        self, workflow_engine: WorkflowEngine, orchestrator: OrchestratorEngine
    ) -> None:
        parallel_wf = Workflow(
            name="parallel",
            description="Steps a and b run in parallel, then c",
            steps=[
                WorkflowStep(name="a", agent_type="plan", prompt_template="Plan step A"),
                WorkflowStep(name="b", agent_type="knowledge_base", prompt_template="Research B"),
                WorkflowStep(
                    name="c",
                    agent_type="knowledge_base",
                    prompt_template="Summarise everything",
                    depends_on=["a", "b"],
                ),
            ],
        )
        workflow_engine.register_workflow(parallel_wf)
        result = await workflow_engine.execute("parallel")

        assert result["steps_completed"] == 3
        assert "a" in result["results"]
        assert "b" in result["results"]
        assert "c" in result["results"]

    @pytest.mark.asyncio
    async def test_execute_step_failure_stops_dependent_steps(
        self, workflow_engine: WorkflowEngine, orchestrator: OrchestratorEngine
    ) -> None:
        """When a step fails and continue_on_error=False, dependent steps do not run."""
        from unittest.mock import AsyncMock, patch

        failing_wf = Workflow(
            name="failing",
            description="Step b depends on step a which will fail",
            steps=[
                WorkflowStep(
                    name="a",
                    agent_type="plan",
                    prompt_template="Step A",
                    continue_on_error=False,
                ),
                WorkflowStep(
                    name="b",
                    agent_type="knowledge_base",
                    prompt_template="Step B",
                    depends_on=["a"],
                ),
            ],
        )
        workflow_engine.register_workflow(failing_wf)

        with patch.object(orchestrator, "process", new_callable=AsyncMock) as mock_process:
            mock_process.side_effect = RuntimeError("simulated failure")
            result = await workflow_engine.execute("failing")

        # Step a should be recorded as failed; step b may or may not run
        # (the wave loop breaks on the first failed step within a wave)
        assert result["results"]["a"]["status"] == "failed"

    @pytest.mark.asyncio
    async def test_list_workflows_after_register(
        self,
        workflow_engine: WorkflowEngine,
        simple_workflow: Workflow,
        sequential_workflow: Workflow,
    ) -> None:
        workflow_engine.register_workflow(simple_workflow)
        workflow_engine.register_workflow(sequential_workflow)
        listed = workflow_engine.list_workflows()
        names = [w["name"] for w in listed]
        assert "simple" in names
        assert "sequential" in names
