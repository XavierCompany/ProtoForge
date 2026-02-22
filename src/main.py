"""ProtoForge — Main entry point.

Wires together:
- Orchestrator engine with intent router
- All 7 specialized subagents
- MCP server for skills distribution
- Agent catalog for registry/discovery
- Workflow engine for bundled workflows
- FastAPI HTTP server with Agent Inspector
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import structlog
import typer
import uvicorn

from src.agents import (
    CodeResearchAgent,
    DataAnalysisAgent,
    KnowledgeBaseAgent,
    LogAnalysisAgent,
    PlanAgent,
    RemediationAgent,
    SecuritySentinelAgent,
)
from src.config import get_settings
from src.mcp.server import MCPSkillServer
from src.mcp.skills import SkillLoader
from src.orchestrator.engine import OrchestratorEngine
from src.orchestrator.router import AgentType
from src.registry.catalog import AgentCatalog, AgentRegistration
from src.registry.workflows import WorkflowEngine, WorkflowLoader
from src.server import create_app

logger = structlog.get_logger(__name__)

cli_app = typer.Typer(name="protoforge", help="ProtoForge Multi-Agent Orchestrator")


def bootstrap() -> tuple:
    """Bootstrap all components and wire them together.

    Returns (app, orchestrator, mcp_server, catalog, workflow_engine)
    """
    settings = get_settings()

    # 1. Create orchestrator
    orchestrator = OrchestratorEngine()

    # 2. Register all 7 subagents
    agent_map: dict[AgentType, tuple] = {
        AgentType.PLAN: (
            PlanAgent(), "Plan Agent",
            "Task planning and decomposition",
        ),
        AgentType.LOG_ANALYSIS: (
            LogAnalysisAgent(), "Log Analysis Agent",
            "Log parsing and error analysis",
        ),
        AgentType.CODE_RESEARCH: (
            CodeResearchAgent(), "Code Research Agent",
            "Code search and analysis",
        ),
        AgentType.REMEDIATION: (
            RemediationAgent(), "Remediation Agent",
            "Bug fixes and patches",
        ),
        AgentType.KNOWLEDGE_BASE: (
            KnowledgeBaseAgent(), "Knowledge Base Agent",
            "Documentation and knowledge retrieval",
        ),
        AgentType.DATA_ANALYSIS: (
            DataAnalysisAgent(), "Data Analysis Agent",
            "Data analysis and metrics",
        ),
        AgentType.SECURITY_SENTINEL: (
            SecuritySentinelAgent(), "Security Sentinel Agent",
            "Security scanning and audits",
        ),
    }

    for agent_type, (agent, _name, _desc) in agent_map.items():
        orchestrator.register_agent(agent_type, agent)

    # 3. Load skills and create MCP server
    skills_dir = Path(settings.mcp.skills_dir)
    skills = SkillLoader.load_from_directory(skills_dir)
    mcp_server = MCPSkillServer()
    mcp_server.load_skills(skills_dir)

    # Wire MCP tool calls to orchestrator
    async def handle_tool_call(tool_name: str, arguments: dict, agent_type: str | None) -> str:
        # Build a message from the tool call
        msg = f"[Tool: {tool_name}] {' '.join(f'{k}={v}' for k, v in arguments.items())}"
        return await orchestrator.process(msg)

    mcp_server.set_call_handler(handle_tool_call)

    # 4. Create agent catalog
    catalog = AgentCatalog(storage_path=settings.registry_path)
    for agent_type, (_agent, name, desc) in agent_map.items():
        catalog.register_agent(AgentRegistration(
            agent_type=agent_type.value,
            name=name,
            description=desc,
            skills=[s.name for s in skills if s.agent_type == agent_type.value],
            tags=[agent_type.value],
        ))
    catalog.populate_from_skills(skills)

    # 5. Load workflows
    workflow_engine = WorkflowEngine(orchestrator)
    workflows_dir = Path("workflows")
    if workflows_dir.exists():
        for workflow in WorkflowLoader.load_from_directory(workflows_dir):
            workflow_engine.register_workflow(workflow)

    # 6. Create FastAPI app
    app = create_app(orchestrator, mcp_server, catalog, workflow_engine)

    logger.info(
        "protoforge_bootstrapped",
        agents=len(agent_map),
        skills=len(skills),
        workflows=len(workflow_engine.list_workflows()),
        provider=settings.llm.active_provider.value,
    )

    return app, orchestrator, mcp_server, catalog, workflow_engine


@cli_app.command()
def serve(
    host: str = typer.Option("0.0.0.0", help="Host to bind to"),
    port: int = typer.Option(8080, help="Port to bind to"),
    reload: bool = typer.Option(False, help="Enable auto-reload for development"),
) -> None:
    """Start the ProtoForge HTTP server."""
    app, *_ = bootstrap()

    typer.echo(f"\n🚀 ProtoForge starting on http://{host}:{port}")
    typer.echo(f"📊 Agent Inspector: http://{host}:{port}/inspector")
    typer.echo(f"🔧 MCP endpoint:   http://{host}:{port}/mcp")
    typer.echo(f"💬 Chat endpoint:   http://{host}:{port}/chat\n")

    uvicorn.run(app, host=host, port=port, reload=reload)


@cli_app.command()
def chat() -> None:
    """Interactive chat mode with the orchestrator."""
    _, orchestrator, *_ = bootstrap()

    typer.echo("\n💬 ProtoForge Interactive Chat")
    typer.echo("Type 'quit' to exit, 'reset' to start a new session\n")

    async def chat_loop() -> None:
        while True:
            try:
                message = input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                break

            if not message:
                continue
            if message.lower() == "quit":
                break
            if message.lower() == "reset":
                orchestrator.reset_context()
                typer.echo("🔄 Session reset\n")
                continue

            response = await orchestrator.process(message)
            typer.echo(f"\nProtoForge: {response}\n")

    asyncio.run(chat_loop())


@cli_app.command()
def status() -> None:
    """Show current ProtoForge status."""
    _, orchestrator, mcp_server, catalog, workflow_engine = bootstrap()

    from rich.console import Console
    from rich.table import Table

    console = Console()

    # Agents table
    table = Table(title="Registered Agents")
    table.add_column("Type", style="cyan")
    table.add_column("Name", style="green")
    table.add_column("Skills", style="yellow")
    table.add_column("Status", style="bold")

    for agent in catalog.list_agents():
        table.add_row(
            agent.agent_type,
            agent.name,
            str(len(agent.skills)),
            f"[green]{agent.status}[/green]",
        )

    console.print(table)

    # Skills table
    skills_table = Table(title="Skill Catalog")
    skills_table.add_column("Skill", style="cyan")
    skills_table.add_column("Agent", style="green")
    skills_table.add_column("Installed", style="yellow")

    for skill in catalog.search_catalog():
        skills_table.add_row(
            skill.skill_name,
            skill.agent_type,
            "✅" if skill.installed else "❌",
        )

    console.print(skills_table)

    # Workflows table
    wf_table = Table(title="Workflows")
    wf_table.add_column("Name", style="cyan")
    wf_table.add_column("Steps", style="green")
    wf_table.add_column("Tags", style="yellow")

    for wf in workflow_engine.list_workflows():
        wf_table.add_row(wf["name"], str(wf["steps"]), ", ".join(wf["tags"]))

    console.print(wf_table)


if __name__ == "__main__":
    cli_app()
