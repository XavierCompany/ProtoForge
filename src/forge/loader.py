"""Forge Loader — discovers agents, skills, prompts, and workflows from forge/ directory.

The loader walks the forge/ directory tree and builds an in-memory registry of all
agent manifests (agent.yaml), prompts (.md), skills (.yaml), instructions (.md),
and workflows (.yaml).  It also discovers dynamic contributions from forge/contrib/.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog
import yaml

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class AgentManifest:
    """Parsed agent.yaml manifest."""

    id: str
    name: str
    type: str  # coordinator | specialist
    version: str
    description: str
    context_budget: dict[str, Any] = field(default_factory=dict)
    skills: list[str] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)
    instructions: list[str] = field(default_factory=list)
    subagents: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    # Resolved absolute paths (populated by loader)
    base_path: Path | None = None
    resolved_prompts: dict[str, str] = field(default_factory=dict)
    resolved_instructions: dict[str, str] = field(default_factory=dict)


@dataclass
class ForgeRegistry:
    """In-memory registry of everything discovered in forge/."""

    agents: dict[str, AgentManifest] = field(default_factory=dict)
    coordinator: AgentManifest | None = None
    shared_prompts: dict[str, str] = field(default_factory=dict)
    shared_instructions: dict[str, str] = field(default_factory=dict)
    workflows: list[dict[str, Any]] = field(default_factory=list)
    skills: list[dict[str, Any]] = field(default_factory=list)
    context_config: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

class ForgeLoader:
    """Discovers and loads the forge/ ecosystem."""

    def __init__(self, forge_root: Path | str = "forge") -> None:
        self.forge_root = Path(forge_root).resolve()
        self.registry = ForgeRegistry()

    # -- Public API ---------------------------------------------------------

    def load(self) -> ForgeRegistry:
        """Full discovery — loads everything and returns the registry."""
        if not self.forge_root.exists():
            logger.warning("forge_root_not_found", path=str(self.forge_root))
            return self.registry

        logger.info("forge_load_start", root=str(self.forge_root))

        self._load_context_config()
        self._load_coordinator()
        self._load_agents()
        self._load_shared()
        self._load_contrib()

        logger.info(
            "forge_load_complete",
            coordinator=self.registry.coordinator.id if self.registry.coordinator else None,
            agents=len(self.registry.agents),
            skills=len(self.registry.skills),
            workflows=len(self.registry.workflows),
            shared_prompts=len(self.registry.shared_prompts),
        )
        return self.registry

    def get_agent(self, agent_id: str) -> AgentManifest | None:
        """Retrieve a loaded agent manifest by ID."""
        if self.registry.coordinator and self.registry.coordinator.id == agent_id:
            return self.registry.coordinator
        return self.registry.agents.get(agent_id)

    def list_agent_ids(self) -> list[str]:
        """Return all loaded agent IDs (coordinator + specialists)."""
        ids: list[str] = []
        if self.registry.coordinator:
            ids.append(self.registry.coordinator.id)
        ids.extend(sorted(self.registry.agents.keys()))
        return ids

    # -- Private loaders ----------------------------------------------------

    def _load_context_config(self) -> None:
        path = self.forge_root / "_context_window.yaml"
        if path.exists():
            with open(path) as f:
                self.registry.context_config = yaml.safe_load(f) or {}
            logger.debug("context_config_loaded", path=str(path))

    def _load_coordinator(self) -> None:
        plan_dir = self.forge_root / "plan"
        if not plan_dir.exists():
            return
        manifest = self._load_agent_dir(plan_dir)
        if manifest:
            self.registry.coordinator = manifest
            # Also collect skills from the coordinator
            self._collect_skills(plan_dir)
            self._collect_workflows(plan_dir / "workflows")

    def _load_agents(self) -> None:
        agents_dir = self.forge_root / "agents"
        if not agents_dir.exists():
            return
        for agent_dir in sorted(agents_dir.iterdir()):
            if agent_dir.is_dir() and (agent_dir / "agent.yaml").exists():
                manifest = self._load_agent_dir(agent_dir)
                if manifest:
                    self.registry.agents[manifest.id] = manifest
                    self._collect_skills(agent_dir)

    def _load_shared(self) -> None:
        shared_dir = self.forge_root / "shared"
        if not shared_dir.exists():
            return

        # Shared prompts
        for md in sorted((shared_dir / "prompts").glob("*.md")) if (shared_dir / "prompts").exists() else []:
            self.registry.shared_prompts[md.stem] = md.read_text(encoding="utf-8")

        # Shared instructions
        instr_dir = shared_dir / "instructions"
        for md in sorted(instr_dir.glob("*.md")) if instr_dir.exists() else []:
            self.registry.shared_instructions[md.stem] = md.read_text(encoding="utf-8")

        # Shared workflows
        self._collect_workflows(shared_dir / "workflows")

    def _load_contrib(self) -> None:
        """Discover dynamic contributions from forge/contrib/."""
        contrib_dir = self.forge_root / "contrib"
        if not contrib_dir.exists():
            return

        # Contributed agents
        agents_dir = contrib_dir / "agents"
        if agents_dir.exists():
            for agent_dir in sorted(agents_dir.iterdir()):
                if agent_dir.is_dir() and (agent_dir / "agent.yaml").exists():
                    manifest = self._load_agent_dir(agent_dir)
                    if manifest:
                        if manifest.id in self.registry.agents:
                            logger.warning("contrib_agent_id_collision", id=manifest.id)
                        else:
                            self.registry.agents[manifest.id] = manifest
                            self._collect_skills(agent_dir)
                            logger.info("contrib_agent_loaded", id=manifest.id)

        # Contributed skills
        self._collect_skills(contrib_dir)

        # Contributed workflows
        self._collect_workflows(contrib_dir / "workflows")

    # -- Helpers ------------------------------------------------------------

    def _load_agent_dir(self, agent_dir: Path) -> AgentManifest | None:
        """Parse an agent.yaml and resolve its prompts/instructions."""
        manifest_path = agent_dir / "agent.yaml"
        if not manifest_path.exists():
            return None

        try:
            with open(manifest_path) as f:
                data = yaml.safe_load(f)

            manifest = AgentManifest(
                id=data["id"],
                name=data["name"],
                type=data.get("type", "specialist"),
                version=data.get("version", "1.0.0"),
                description=data.get("description", ""),
                context_budget=data.get("context_budget", {}),
                skills=data.get("skills", []),
                prompts=data.get("prompts", []),
                instructions=data.get("instructions", []),
                subagents=data.get("subagents", []),
                tags=data.get("tags", []),
                base_path=agent_dir,
            )

            # Resolve prompts (read .md files)
            for rel in manifest.prompts:
                p = agent_dir / rel
                if p.exists():
                    manifest.resolved_prompts[p.stem] = p.read_text(encoding="utf-8")

            # Resolve instructions (read .md files)
            for rel in manifest.instructions:
                p = agent_dir / rel
                if p.exists():
                    manifest.resolved_instructions[p.stem] = p.read_text(encoding="utf-8")

            logger.info("agent_manifest_loaded", id=manifest.id, type=manifest.type, path=str(agent_dir))
            return manifest

        except Exception as exc:
            logger.error("agent_manifest_load_failed", path=str(manifest_path), error=str(exc))
            return None

    def _collect_skills(self, base_dir: Path) -> None:
        """Collect all .yaml skill files from a skills/ subfolder."""
        skills_dir = base_dir / "skills"
        if not skills_dir.exists():
            return
        for yaml_file in sorted(skills_dir.glob("*.yaml")):
            try:
                with open(yaml_file) as f:
                    data = yaml.safe_load(f)
                if data and "name" in data:
                    data["_source_path"] = str(yaml_file)
                    self.registry.skills.append(data)
            except Exception as exc:
                logger.error("skill_load_failed", file=str(yaml_file), error=str(exc))

    def _collect_workflows(self, workflows_dir: Path) -> None:
        """Collect all .yaml workflow files from a directory."""
        if not workflows_dir or not workflows_dir.exists():
            return
        for yaml_file in sorted(workflows_dir.glob("*.yaml")):
            try:
                with open(yaml_file) as f:
                    data = yaml.safe_load(f)
                if data and "name" in data:
                    data["_source_path"] = str(yaml_file)
                    self.registry.workflows.append(data)
            except Exception as exc:
                logger.error("workflow_load_failed", file=str(yaml_file), error=str(exc))
