"""Contribution Manager — CRUD for dynamic agents, skills, prompts, and workflows.

Manages the forge/contrib/ directory and maintains an audit log of all changes.
Validates contributions against the required schemas before accepting them.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any

import structlog
import yaml

logger = structlog.get_logger(__name__)


class ValidationError(Exception):
    """Raised when a contribution fails schema validation."""


class ContributionManager:
    """CRUD operations for dynamic forge/ contributions with audit logging."""

    REQUIRED_AGENT_FIELDS = {"id", "name", "type", "description"}
    REQUIRED_SKILL_FIELDS = {"name", "description", "parameters"}
    REQUIRED_WORKFLOW_FIELDS = {"name", "steps"}

    def __init__(self, forge_root: Path | str = "forge") -> None:
        self.forge_root = Path(forge_root).resolve()
        self.contrib_dir = self.forge_root / "contrib"
        self.audit_path = self.contrib_dir / "audit_log.yaml"

    # -- Agent CRUD ---------------------------------------------------------

    def create_agent(
        self,
        agent_id: str,
        manifest: dict[str, Any],
        system_prompt: str,
        *,
        author: str = "system",
    ) -> Path:
        """Create a new contributed agent package.

        Args:
            agent_id: Unique agent identifier.
            manifest: Agent manifest dict (must have id, name, type, description).
            system_prompt: Markdown system prompt content.
            author: Who is creating this contribution.

        Returns:
            Path to the created agent directory.
        """
        self._validate_agent(manifest)
        agent_dir = self.contrib_dir / "agents" / agent_id
        if agent_dir.exists():
            msg = f"Agent '{agent_id}' already exists in contrib/"
            raise FileExistsError(msg)

        # Create directory structure
        agent_dir.mkdir(parents=True, exist_ok=True)
        (agent_dir / "prompts").mkdir(exist_ok=True)
        (agent_dir / "skills").mkdir(exist_ok=True)
        (agent_dir / "instructions").mkdir(exist_ok=True)

        # Write manifest
        manifest.setdefault("id", agent_id)
        with open(agent_dir / "agent.yaml", "w") as f:
            yaml.dump(manifest, f, default_flow_style=False, sort_keys=False)

        # Write system prompt
        (agent_dir / "prompts" / "system.md").write_text(system_prompt, encoding="utf-8")

        desc = f"Created agent: {manifest.get('name', agent_id)}"
        self._audit("create_agent", f"contrib/agents/{agent_id}", author, desc)
        logger.info("contrib_agent_created", id=agent_id, author=author)
        return agent_dir

    def update_agent(
        self,
        agent_id: str,
        manifest: dict[str, Any] | None = None,
        system_prompt: str | None = None,
        *,
        author: str = "system",
    ) -> None:
        """Update an existing contributed agent."""
        agent_dir = self.contrib_dir / "agents" / agent_id
        if not agent_dir.exists():
            msg = f"Agent '{agent_id}' not found in contrib/"
            raise FileNotFoundError(msg)

        if manifest:
            self._validate_agent(manifest)
            with open(agent_dir / "agent.yaml", "w") as f:
                yaml.dump(manifest, f, default_flow_style=False, sort_keys=False)

        if system_prompt:
            (agent_dir / "prompts" / "system.md").write_text(system_prompt, encoding="utf-8")

        self._audit("update_agent", f"contrib/agents/{agent_id}", author, f"Updated agent: {agent_id}")
        logger.info("contrib_agent_updated", id=agent_id, author=author)

    def delete_agent(self, agent_id: str, *, author: str = "system") -> None:
        """Delete a contributed agent (soft-delete via audit log, then remove files)."""
        agent_dir = self.contrib_dir / "agents" / agent_id
        if not agent_dir.exists():
            msg = f"Agent '{agent_id}' not found in contrib/"
            raise FileNotFoundError(msg)

        self._audit("delete_agent", f"contrib/agents/{agent_id}", author, f"Deleted agent: {agent_id}")
        import shutil

        shutil.rmtree(agent_dir)
        logger.info("contrib_agent_deleted", id=agent_id, author=author)

    # -- Skill CRUD ---------------------------------------------------------

    def create_skill(self, skill_data: dict[str, Any], *, author: str = "system") -> Path:
        """Create a contributed skill YAML."""
        self._validate_skill(skill_data)
        name = skill_data["name"]
        path = self.contrib_dir / "skills" / f"{name}.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)

        if path.exists():
            msg = f"Skill '{name}' already exists in contrib/skills/"
            raise FileExistsError(msg)

        with open(path, "w") as f:
            yaml.dump(skill_data, f, default_flow_style=False, sort_keys=False)

        self._audit("create_skill", f"contrib/skills/{name}.yaml", author, f"Created skill: {name}")
        logger.info("contrib_skill_created", name=name, author=author)
        return path

    def delete_skill(self, skill_name: str, *, author: str = "system") -> None:
        """Delete a contributed skill."""
        path = self.contrib_dir / "skills" / f"{skill_name}.yaml"
        if not path.exists():
            msg = f"Skill '{skill_name}' not found in contrib/skills/"
            raise FileNotFoundError(msg)

        self._audit("delete_skill", f"contrib/skills/{skill_name}.yaml", author, f"Deleted skill: {skill_name}")
        path.unlink()
        logger.info("contrib_skill_deleted", name=skill_name, author=author)

    # -- Workflow CRUD ------------------------------------------------------

    def create_workflow(self, workflow_data: dict[str, Any], *, author: str = "system") -> Path:
        """Create a contributed workflow YAML."""
        self._validate_workflow(workflow_data)
        name = workflow_data["name"]
        path = self.contrib_dir / "workflows" / f"{name}.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)

        if path.exists():
            msg = f"Workflow '{name}' already exists in contrib/workflows/"
            raise FileExistsError(msg)

        with open(path, "w") as f:
            yaml.dump(workflow_data, f, default_flow_style=False, sort_keys=False)

        self._audit("create_workflow", f"contrib/workflows/{name}.yaml", author, f"Created workflow: {name}")
        logger.info("contrib_workflow_created", name=name, author=author)
        return path

    def delete_workflow(self, workflow_name: str, *, author: str = "system") -> None:
        """Delete a contributed workflow."""
        path = self.contrib_dir / "workflows" / f"{workflow_name}.yaml"
        if not path.exists():
            msg = f"Workflow '{workflow_name}' not found in contrib/workflows/"
            raise FileNotFoundError(msg)

        self._audit(
            "delete_workflow", f"contrib/workflows/{workflow_name}.yaml",
            author, f"Deleted workflow: {workflow_name}",
        )
        path.unlink()
        logger.info("contrib_workflow_deleted", name=workflow_name, author=author)

    # -- Listing ------------------------------------------------------------

    def list_contributions(self) -> dict[str, list[str]]:
        """List all contributed agents, skills, and workflows."""
        result: dict[str, list[str]] = {"agents": [], "skills": [], "workflows": []}

        agents_dir = self.contrib_dir / "agents"
        if agents_dir.exists():
            result["agents"] = sorted(d.name for d in agents_dir.iterdir() if d.is_dir())

        skills_dir = self.contrib_dir / "skills"
        if skills_dir.exists():
            result["skills"] = sorted(f.stem for f in skills_dir.glob("*.yaml"))

        workflows_dir = self.contrib_dir / "workflows"
        if workflows_dir.exists():
            result["workflows"] = sorted(f.stem for f in workflows_dir.glob("*.yaml"))

        return result

    def get_audit_log(self) -> list[dict[str, Any]]:
        """Return the full audit log."""
        if not self.audit_path.exists():
            return []
        with open(self.audit_path) as f:
            data = yaml.safe_load(f) or {}
        return data.get("entries", [])

    # -- Validation ---------------------------------------------------------

    def _validate_agent(self, manifest: dict[str, Any]) -> None:
        missing = self.REQUIRED_AGENT_FIELDS - set(manifest.keys())
        if missing:
            msg = f"Agent manifest missing required fields: {missing}"
            raise ValidationError(msg)

    def _validate_skill(self, data: dict[str, Any]) -> None:
        missing = self.REQUIRED_SKILL_FIELDS - set(data.keys())
        if missing:
            msg = f"Skill definition missing required fields: {missing}"
            raise ValidationError(msg)

    def _validate_workflow(self, data: dict[str, Any]) -> None:
        missing = self.REQUIRED_WORKFLOW_FIELDS - set(data.keys())
        if missing:
            msg = f"Workflow definition missing required fields: {missing}"
            raise ValidationError(msg)

    # -- Audit logging ------------------------------------------------------

    def _audit(self, action: str, path: str, author: str, description: str) -> None:
        """Append an entry to the audit log."""
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)

        if self.audit_path.exists():
            with open(self.audit_path) as f:
                data = yaml.safe_load(f) or {}
        else:
            data = {"version": "1.0.0", "entries": []}

        entry = {
            "timestamp": datetime.datetime.now(tz=datetime.UTC).isoformat(),
            "action": action,
            "path": path,
            "author": author,
            "description": description,
        }
        data.setdefault("entries", []).append(entry)

        with open(self.audit_path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)

        logger.debug("audit_entry", action=action, path=path, author=author)
