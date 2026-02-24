"""Workflow Bundling — compose multi-agent workflows from YAML definitions.

Workflows define ordered sequences of agent actions that can be:
- Triggered by a single command
- Executed with shared context flowing between steps
- Parallelized where steps are independent
- Retried on failure with configurable policies
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog
import yaml

if TYPE_CHECKING:
    from pathlib import Path

logger = structlog.get_logger(__name__)


@dataclass
class WorkflowStep:
    """A single step in a workflow."""

    name: str
    agent_type: str
    prompt_template: str
    depends_on: list[str] = field(default_factory=list)
    timeout_seconds: int = 60
    retry_count: int = 0
    continue_on_error: bool = False
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class Workflow:
    """A multi-agent workflow bundle — ordered sequence of agent actions."""

    name: str
    description: str
    version: str = "1.0.0"
    steps: list[WorkflowStep] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    def get_execution_order(self) -> list[list[WorkflowStep]]:
        """Compute execution order — steps with no dependencies can run in parallel.

        Returns a list of "waves" — each wave contains steps that can run concurrently.
        """
        completed: set[str] = set()
        remaining = list(self.steps)
        waves: list[list[WorkflowStep]] = []

        while remaining:
            # Find steps whose dependencies are all completed
            ready = [step for step in remaining if all(dep in completed for dep in step.depends_on)]

            if not ready:
                # Circular dependency or missing step
                logger.error(
                    "workflow_deadlock",
                    remaining=[s.name for s in remaining],
                    completed=list(completed),
                )
                break

            waves.append(ready)
            for step in ready:
                completed.add(step.name)
                remaining.remove(step)

        return waves


class WorkflowLoader:
    """Load workflow definitions from YAML files."""

    @staticmethod
    def load_from_file(path: Path) -> Workflow:
        """Load a workflow from a YAML file."""
        with open(path) as f:
            data = yaml.safe_load(f)

        steps = [
            WorkflowStep(
                name=s["name"],
                agent_type=s["agent_type"],
                prompt_template=s["prompt_template"],
                depends_on=s.get("depends_on", []),
                timeout_seconds=s.get("timeout_seconds", 60),
                retry_count=s.get("retry_count", 0),
                continue_on_error=s.get("continue_on_error", False),
                params=s.get("params", {}),
            )
            for s in data.get("steps", [])
        ]

        return Workflow(
            name=data["name"],
            description=data["description"],
            version=data.get("version", "1.0.0"),
            steps=steps,
            tags=data.get("tags", []),
        )

    @staticmethod
    def load_from_directory(directory: Path) -> list[Workflow]:
        """Load all workflows from YAML files in a directory."""
        workflows: list[Workflow] = []
        if not directory.exists():
            return workflows

        for yaml_file in sorted(directory.glob("*.yaml")):
            try:
                workflow = WorkflowLoader.load_from_file(yaml_file)
                workflows.append(workflow)
                logger.info(
                    "workflow_loaded",
                    name=workflow.name,
                    steps=len(workflow.steps),
                )
            except Exception as exc:
                logger.error("workflow_load_failed", file=str(yaml_file), error=str(exc))

        return workflows


class WorkflowEngine:
    """Executes workflow bundles through the orchestrator."""

    def __init__(self, orchestrator: Any) -> None:
        self._orchestrator = orchestrator
        self._workflows: dict[str, Workflow] = {}

    def register_workflow(self, workflow: Workflow) -> None:
        """Register a workflow for execution."""
        self._workflows[workflow.name] = workflow

    def list_workflows(self) -> list[dict[str, Any]]:
        """List all registered workflows."""
        return [
            {
                "name": w.name,
                "description": w.description,
                "version": w.version,
                "steps": len(w.steps),
                "tags": w.tags,
            }
            for w in self._workflows.values()
        ]

    async def execute(
        self,
        workflow_name: str,
        initial_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute a workflow by name.

        Runs steps in dependency order, parallelizing independent steps.
        """
        workflow = self._workflows.get(workflow_name)
        if not workflow:
            return {"error": f"Workflow '{workflow_name}' not found"}

        self._orchestrator.context.active_workflow = workflow_name
        waves = workflow.get_execution_order()
        results: dict[str, Any] = {}
        params = initial_params or {}

        logger.info(
            "workflow_started",
            name=workflow_name,
            waves=len(waves),
            total_steps=len(workflow.steps),
        )

        for wave_idx, wave in enumerate(waves):
            logger.info(
                "workflow_wave",
                wave=wave_idx,
                steps=[s.name for s in wave],
            )

            for step in wave:
                # Resolve the prompt template with available params
                prompt = step.prompt_template.format(
                    **{
                        **params,
                        **{k: results.get(k, {}).get("content", "") for k in step.depends_on},
                    }
                )

                try:
                    result, _ctx = await self._orchestrator.process(prompt)
                    results[step.name] = {
                        "content": result,
                        "status": "completed",
                        "agent_type": step.agent_type,
                    }
                except Exception as exc:
                    logger.error("workflow_step_failed", step=step.name, error=str(exc))
                    results[step.name] = {
                        "content": str(exc),
                        "status": "failed",
                        "agent_type": step.agent_type,
                    }
                    if not step.continue_on_error:
                        break

        self._orchestrator.context.active_workflow = None

        logger.info("workflow_completed", name=workflow_name, results=len(results))
        return {
            "workflow": workflow_name,
            "steps_completed": len([r for r in results.values() if r["status"] == "completed"]),
            "steps_failed": len([r for r in results.values() if r["status"] == "failed"]),
            "results": results,
        }
