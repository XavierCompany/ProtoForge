"""Agent Catalog — discovery, registration, and management of agents and skills.

This is the central registry that tracks:
- Available agents and their capabilities
- Installed skills and their versions
- Agent health and usage metrics
- Dependency relationships between agents
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from pathlib import Path

    from src.mcp.skills import Skill

logger = structlog.get_logger(__name__)


@dataclass
class AgentRegistration:
    """Registration entry for an agent in the catalog."""

    agent_type: str
    name: str
    description: str
    version: str = "1.0.0"
    status: str = "active"  # active, disabled, degraded
    skills: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    registered_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    usage_count: int = 0
    avg_latency_ms: float = 0.0
    error_rate: float = 0.0


@dataclass
class CatalogEntry:
    """An entry in the skill catalog — installable skill package."""

    skill_name: str
    description: str
    agent_type: str
    version: str
    tags: list[str] = field(default_factory=list)
    installed: bool = False
    source: str = "local"  # local, registry, git


class AgentCatalog:
    """Central catalog for agent and skill discovery.

    Provides:
    - Agent registration and lookup
    - Skill catalog with install/uninstall
    - Health tracking and usage metrics
    - Search and filtering
    - Persistence to disk
    """

    def __init__(self, storage_path: Path | None = None) -> None:
        self._agents: dict[str, AgentRegistration] = {}
        self._catalog: dict[str, CatalogEntry] = {}
        self._storage_path = storage_path

        if storage_path and storage_path.exists():
            self._load()

    # ── Agent Registration ──────────────────────────────────────────

    def register_agent(self, registration: AgentRegistration) -> None:
        """Register an agent in the catalog."""
        self._agents[registration.agent_type] = registration
        logger.info(
            "agent_registered_in_catalog",
            agent=registration.agent_type,
            skills=registration.skills,
        )
        self._persist()

    def unregister_agent(self, agent_type: str) -> bool:
        """Remove an agent from the catalog."""
        if agent_type in self._agents:
            del self._agents[agent_type]
            self._persist()
            return True
        return False

    def get_agent(self, agent_type: str) -> AgentRegistration | None:
        """Look up an agent by type."""
        return self._agents.get(agent_type)

    def list_agents(
        self,
        status: str | None = None,
        tag: str | None = None,
    ) -> list[AgentRegistration]:
        """List agents with optional filtering."""
        agents = list(self._agents.values())
        if status:
            agents = [a for a in agents if a.status == status]
        if tag:
            agents = [a for a in agents if tag in a.tags]
        return agents

    def update_agent_metrics(
        self,
        agent_type: str,
        latency_ms: float,
        is_error: bool = False,
    ) -> None:
        """Update usage metrics for an agent."""
        agent = self._agents.get(agent_type)
        if not agent:
            return

        agent.usage_count += 1
        # Rolling average for latency
        n = agent.usage_count
        agent.avg_latency_ms = ((agent.avg_latency_ms * (n - 1)) + latency_ms) / n
        # Rolling error rate
        if is_error:
            agent.error_rate = ((agent.error_rate * (n - 1)) + 1.0) / n
        else:
            agent.error_rate = (agent.error_rate * (n - 1)) / n

        self._persist()

    # ── Skill Catalog ───────────────────────────────────────────────

    def add_to_catalog(self, entry: CatalogEntry) -> None:
        """Add a skill to the catalog."""
        self._catalog[entry.skill_name] = entry
        logger.info("skill_added_to_catalog", skill=entry.skill_name)
        self._persist()

    def install_skill(self, skill_name: str) -> bool:
        """Mark a skill as installed."""
        entry = self._catalog.get(skill_name)
        if entry:
            entry.installed = True
            self._persist()
            return True
        return False

    def uninstall_skill(self, skill_name: str) -> bool:
        """Mark a skill as uninstalled."""
        entry = self._catalog.get(skill_name)
        if entry:
            entry.installed = False
            self._persist()
            return True
        return False

    def search_catalog(
        self,
        query: str = "",
        agent_type: str | None = None,
        tag: str | None = None,
        installed_only: bool = False,
    ) -> list[CatalogEntry]:
        """Search the skill catalog."""
        results = list(self._catalog.values())

        if query:
            q = query.lower()
            results = [e for e in results if q in e.skill_name.lower() or q in e.description.lower()]
        if agent_type:
            results = [e for e in results if e.agent_type == agent_type]
        if tag:
            results = [e for e in results if tag in e.tags]
        if installed_only:
            results = [e for e in results if e.installed]

        return results

    def populate_from_skills(self, skills: list[Skill]) -> None:
        """Bulk-populate the catalog from loaded skills."""
        for skill in skills:
            self.add_to_catalog(
                CatalogEntry(
                    skill_name=skill.name,
                    description=skill.description,
                    agent_type=skill.agent_type,
                    version=skill.version,
                    tags=skill.tags,
                    installed=True,
                    source="local",
                )
            )

    # ── Status ──────────────────────────────────────────────────────

    def get_status(self) -> dict[str, Any]:
        """Return catalog status summary."""
        return {
            "total_agents": len(self._agents),
            "active_agents": len([a for a in self._agents.values() if a.status == "active"]),
            "total_skills": len(self._catalog),
            "installed_skills": len([s for s in self._catalog.values() if s.installed]),
        }

    # ── Persistence ─────────────────────────────────────────────────

    def _persist(self) -> None:
        """Save catalog state to disk."""
        if not self._storage_path:
            return

        self._storage_path.mkdir(parents=True, exist_ok=True)
        data = {
            "agents": {k: asdict(v) for k, v in self._agents.items()},
            "catalog": {k: asdict(v) for k, v in self._catalog.items()},
        }

        catalog_file = self._storage_path / "catalog.json"
        with open(catalog_file, "w") as f:
            json.dump(data, f, indent=2)

    def _load(self) -> None:
        """Load catalog state from disk."""
        catalog_file = self._storage_path / "catalog.json"
        if not catalog_file.exists():
            return

        try:
            with open(catalog_file) as f:
                data = json.load(f)

            for key, agent_data in data.get("agents", {}).items():
                self._agents[key] = AgentRegistration(**agent_data)
            for key, entry_data in data.get("catalog", {}).items():
                self._catalog[key] = CatalogEntry(**entry_data)

            logger.info(
                "catalog_loaded",
                agents=len(self._agents),
                skills=len(self._catalog),
            )
        except Exception as exc:
            logger.error("catalog_load_failed", error=str(exc))
