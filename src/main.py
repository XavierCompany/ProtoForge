"""ProtoForge — Main entry point.

Wires together:
- Orchestrator engine with intent router
- All subagents (specialized + generic, built from forge manifests)
- MCP server for skills distribution
- Agent catalog for registry/discovery
- Workflow engine for bundled workflows
- Forge ecosystem (agents, prompts, skills, workflows, context budgets)
- Governance Guardian (context window + skill cap enforcement with HITL)
- FastAPI HTTP server with Agent Inspector
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import structlog
import typer
import uvicorn

from src.agents.generic import GenericAgent
from src.agents.github_tracker_agent import GitHubTrackerAgent
from src.agents.knowledge_base_agent import KnowledgeBaseAgent
from src.agents.log_analysis_agent import LogAnalysisAgent
from src.agents.plan_agent import PlanAgent
from src.agents.remediation_agent import RemediationAgent
from src.agents.security_sentinel_agent import SecuritySentinelAgent
from src.agents.sub_plan_agent import SubPlanAgent
from src.agents.workiq_agent import WorkIQAgent
from src.config import get_settings
from src.forge.context_budget import ContextBudgetManager
from src.forge.loader import AgentManifest, ForgeLoader
from src.governance.guardian import GovernanceGuardian
from src.governance.selector import GovernanceSelector
from src.mcp.server import MCPSkillServer
from src.mcp.skills import SkillLoader
from src.orchestrator.engine import OrchestratorEngine
from src.orchestrator.plan_selector import PlanSelector
from src.orchestrator.router import AgentType
from src.registry.catalog import AgentCatalog, AgentRegistration
from src.registry.workflows import WorkflowEngine, WorkflowLoader
from src.server import create_app
from src.workiq.client import WorkIQClient
from src.workiq.selector import WorkIQSelector

logger = structlog.get_logger(__name__)

cli_app = typer.Typer(name="protoforge", help="ProtoForge Multi-Agent Orchestrator")

# ── Mapping from forge agent ID → specialised class ────────────────────────
# If an agent has a custom subclass, use it.  Otherwise fall back to
# GenericAgent whose behaviour is driven entirely by the forge manifest.
_SPECIALISED_CLASSES: dict[str, type] = {
    AgentType.PLAN: PlanAgent,
    AgentType.SUB_PLAN: SubPlanAgent,
    AgentType.LOG_ANALYSIS: LogAnalysisAgent,
    AgentType.REMEDIATION: RemediationAgent,
    AgentType.KNOWLEDGE_BASE: KnowledgeBaseAgent,
    AgentType.SECURITY_SENTINEL: SecuritySentinelAgent,
    AgentType.WORKIQ: WorkIQAgent,
    AgentType.GITHUB_TRACKER: GitHubTrackerAgent,
}


def _create_agent_from_manifest(manifest: AgentManifest):
    """Instantiate the right agent class for a forge manifest.

    If the manifest's ``id`` matches a known specialised class, uses it
    and passes the manifest through so the system prompt comes from
    ``forge/``.  Otherwise falls back to :class:`GenericAgent`.
    """
    cls = _SPECIALISED_CLASSES.get(manifest.id)
    if cls is not None:
        return cls.from_manifest(manifest)
    return GenericAgent.from_manifest(manifest)


def _init_governance(
    forge_dir: str,
) -> tuple[dict[str, Any], ContextBudgetManager, GovernanceGuardian, GovernanceSelector, float]:
    """Initialise context budgets and governance components."""
    forge_loader_pre = ForgeLoader(forge_dir)
    context_config = forge_loader_pre.load_context_config()

    budget_manager = ContextBudgetManager(context_config)
    governance_guardian = GovernanceGuardian(
        config=context_config,
        budget_manager=budget_manager,
    )
    hitl_timeout = float(context_config.get("governance", {}).get("hitl", {}).get("timeout_seconds", 120))
    governance_selector = GovernanceSelector(timeout=hitl_timeout)
    return context_config, budget_manager, governance_guardian, governance_selector, hitl_timeout


def _create_orchestrator(
    *,
    forge_registry: Any,
    governance_guardian: GovernanceGuardian,
    governance_selector: GovernanceSelector,
    budget_manager: ContextBudgetManager,
    hitl_timeout: float,
) -> tuple[OrchestratorEngine, WorkIQClient, WorkIQSelector, PlanSelector]:
    """Create orchestrator and HITL selectors."""
    workiq_client = WorkIQClient()
    workiq_selector = WorkIQSelector(timeout=hitl_timeout)
    plan_selector = PlanSelector(timeout=hitl_timeout)
    orchestrator = OrchestratorEngine(
        workiq_client=workiq_client,
        workiq_selector=workiq_selector,
        plan_selector=plan_selector,
        governance_guardian=governance_guardian,
        governance_selector=governance_selector,
        budget_manager=budget_manager,
        forge_registry=forge_registry,
    )
    return orchestrator, workiq_client, workiq_selector, plan_selector


def _register_agents(
    orchestrator: OrchestratorEngine,
    forge_registry: Any,
    *,
    workiq_client: WorkIQClient,
    workiq_selector: WorkIQSelector,
) -> dict[str, tuple[str, str]]:
    """Register coordinator + specialists from forge with fallbacks."""
    agent_descriptions: dict[str, tuple[str, str]] = {}

    if forge_registry.coordinator:
        m = forge_registry.coordinator
        agent = _create_agent_from_manifest(m)
        orchestrator.register_agent(m.id, agent)
        agent_descriptions[m.id] = (m.name, m.description)
    else:
        agent = PlanAgent()
        orchestrator.register_agent(AgentType.PLAN, agent)
        agent_descriptions[AgentType.PLAN] = ("Plan Agent", agent.description)

    for agent_id, manifest in forge_registry.agents.items():
        agent = _create_agent_from_manifest(manifest)
        orchestrator.register_agent(agent_id, agent)
        agent_descriptions[agent_id] = (manifest.name, manifest.description)

    default_agents: dict[str, tuple[type, str, str]] = {
        AgentType.SUB_PLAN: (
            SubPlanAgent,
            "Sub-Plan Agent",
            "Plans prerequisite resource deployment — minimum viable resources only",
        ),
        AgentType.LOG_ANALYSIS: (LogAnalysisAgent, "Log Analysis Agent", "Log parsing and error analysis"),
        AgentType.CODE_RESEARCH: (GenericAgent, "Code Research Agent", "Code search and analysis"),
        AgentType.REMEDIATION: (RemediationAgent, "Remediation Agent", "Bug fixes and patches"),
        AgentType.KNOWLEDGE_BASE: (KnowledgeBaseAgent, "Knowledge Base Agent", "Documentation and knowledge retrieval"),
        AgentType.DATA_ANALYSIS: (GenericAgent, "Data Analysis Agent", "Data analysis and metrics"),
        AgentType.SECURITY_SENTINEL: (SecuritySentinelAgent, "Security Sentinel Agent", "Security scanning and audits"),
        AgentType.WORKIQ: (
            WorkIQAgent,
            "Work IQ Agent",
            "Microsoft 365 organisational context (people, calendar, docs)",
        ),
        AgentType.GITHUB_TRACKER: (
            GitHubTrackerAgent,
            "GitHub Tracker Agent",
            "GitHub commit documentation, issue management, and changelogs",
        ),
    }
    for aid, (cls, name, desc) in default_agents.items():
        if aid not in agent_descriptions:
            if cls is WorkIQAgent:
                agent = WorkIQAgent(
                    agent_id=aid,
                    description=desc,
                    client=workiq_client,
                    selector=workiq_selector,
                )
            else:
                agent = cls(agent_id=aid, description=desc)
            orchestrator.register_agent(aid, agent)
            agent_descriptions[aid] = (name, desc)

    return agent_descriptions


def _load_skills(settings: Any, forge_registry: Any) -> tuple[list[Any], MCPSkillServer]:
    """Load skills from legacy directory and forge-discovered entries."""
    skills_dir = Path(settings.mcp.skills_dir)
    skills = SkillLoader.load_from_directory(skills_dir) if skills_dir.exists() and skills_dir.is_dir() else []

    for forge_skill in forge_registry.skills:
        source = forge_skill.get("_source_path")
        if source:
            try:
                skill = SkillLoader.load_from_file(Path(source))
                if skill.name not in {s.name for s in skills}:
                    skills.append(skill)
            except Exception:
                logger.debug("forge_skill_load_failed", source=source)

    mcp_server = MCPSkillServer()
    mcp_server.load_skills(skills_dir)
    return skills, mcp_server


def _wire_mcp_handler(mcp_server: MCPSkillServer, orchestrator: OrchestratorEngine) -> None:
    """Wire MCP tool-call handling into orchestrator processing."""

    async def handle_tool_call(tool_name: str, arguments: dict, _agent_type: str | None) -> str:
        msg = f"[Tool: {tool_name}] {' '.join(f'{k}={v}' for k, v in arguments.items())}"
        result, _ctx = await orchestrator.process(msg)
        return result

    mcp_server.set_call_handler(handle_tool_call)


def _create_catalog(
    settings: Any,
    agent_descriptions: dict[str, tuple[str, str]],
    skills: list[Any],
) -> AgentCatalog:
    """Create and populate agent catalog."""
    catalog = AgentCatalog(storage_path=settings.registry_path)
    for agent_id, (name, desc) in agent_descriptions.items():
        catalog.register_agent(
            AgentRegistration(
                agent_type=agent_id,
                name=name,
                description=desc,
                skills=[s.name for s in skills if s.agent_type == agent_id],
                tags=[agent_id],
            )
        )
    catalog.populate_from_skills(skills)
    return catalog


def _load_workflows(orchestrator: OrchestratorEngine, forge_registry: Any) -> WorkflowEngine:
    """Load workflows from legacy directory and forge-discovered entries."""
    workflow_engine = WorkflowEngine(orchestrator)
    workflows_dir = Path("workflows")
    if workflows_dir.exists():
        for workflow in WorkflowLoader.load_from_directory(workflows_dir):
            workflow_engine.register_workflow(workflow)

    for forge_wf in forge_registry.workflows:
        source = forge_wf.get("_source_path")
        if source:
            try:
                for wf in WorkflowLoader.load_from_directory(Path(source).parent):
                    if wf.name not in {w["name"] for w in workflow_engine.list_workflows()}:
                        workflow_engine.register_workflow(wf)
            except Exception:
                logger.debug("forge_workflow_load_failed", source=source)
    return workflow_engine


def _resolve_cors_allowed_origins(settings: Any) -> list[str]:
    """Return normalized CORS allowed origins from server settings."""
    cors_allowed_origins = [o.strip() for o in settings.server.cors_allowed_origins.split(",") if o.strip()]
    return cors_allowed_origins or ["*"]


def bootstrap() -> tuple:
    """Bootstrap all components and wire them together.

    Returns (app, orchestrator, mcp_server, catalog, workflow_engine, workiq_selector, plan_selector)
    """
    settings = get_settings()

    # 0. Governance + forge registry
    _context_config, budget_manager, governance_guardian, governance_selector, hitl_timeout = _init_governance(
        settings.forge.forge_dir
    )
    forge_loader = ForgeLoader(settings.forge.forge_dir, governance_guardian=governance_guardian)
    forge_registry = forge_loader.load()

    # 1. Orchestrator + selectors
    orchestrator, workiq_client, workiq_selector, plan_selector = _create_orchestrator(
        forge_registry=forge_registry,
        governance_guardian=governance_guardian,
        governance_selector=governance_selector,
        budget_manager=budget_manager,
        hitl_timeout=hitl_timeout,
    )

    # 2. Agents + skills + MCP + catalog + workflows
    agent_descriptions = _register_agents(
        orchestrator,
        forge_registry,
        workiq_client=workiq_client,
        workiq_selector=workiq_selector,
    )
    skills, mcp_server = _load_skills(settings, forge_registry)
    _wire_mcp_handler(mcp_server, orchestrator)
    catalog = _create_catalog(settings, agent_descriptions, skills)
    workflow_engine = _load_workflows(orchestrator, forge_registry)

    # 3. FastAPI app
    app = create_app(
        orchestrator,
        mcp_server,
        catalog,
        workflow_engine,
        workiq_selector,
        plan_selector,
        governance_selector,
        require_control_plane_api_key=settings.server.require_control_plane_api_key,
        control_plane_api_key=settings.server.control_plane_api_key,
        cors_allowed_origins=_resolve_cors_allowed_origins(settings),
        cors_allow_credentials=settings.server.cors_allow_credentials,
    )

    logger.info(
        "protoforge_bootstrapped",
        agents=len(agent_descriptions),
        skills=len(skills),
        workflows=len(workflow_engine.list_workflows()),
        forge_agents=len(forge_registry.agents) + (1 if forge_registry.coordinator else 0),
        forge_skills=len(forge_registry.skills),
        provider=settings.llm.active_provider.value,
        workiq_available=workiq_client.available,
        governance_enabled=True,
        governance_alerts=len(governance_guardian.unresolved_alerts()),
    )

    return app, orchestrator, mcp_server, catalog, workflow_engine, workiq_selector, plan_selector


@cli_app.command()
def serve(
    host: str = typer.Option("0.0.0.0", help="Host to bind to"),  # noqa: S104
    port: int = typer.Option(8080, help="Port to bind to"),
    reload: bool = typer.Option(False, help="Enable auto-reload for development"),
) -> None:
    """Start the ProtoForge HTTP server."""
    app, *_ = bootstrap()

    typer.echo(f"\n>> ProtoForge starting on http://{host}:{port}")
    typer.echo(f"   Agent Inspector: http://{host}:{port}/inspector")
    typer.echo(f"   MCP endpoint:    http://{host}:{port}/mcp")
    typer.echo(f"   Chat endpoint:   http://{host}:{port}/chat\n")

    uvicorn.run(app, host=host, port=port, reload=reload)


@cli_app.command()
def chat() -> None:
    """Interactive chat mode with the orchestrator."""
    _, orchestrator, *_ = bootstrap()

    typer.echo("\n>> ProtoForge Interactive Chat")
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
                typer.echo("[reset] Session reset\n")
                continue

            response, _ctx = await orchestrator.process(message)
            typer.echo(f"\nProtoForge: {response}\n")

    asyncio.run(chat_loop())


@cli_app.command()
def status() -> None:
    """Show current ProtoForge status."""
    _, _orchestrator, _mcp_server, catalog, workflow_engine, _, _ = bootstrap()

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
