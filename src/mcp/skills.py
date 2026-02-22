"""Skill definitions — YAML-backed skill registry that maps to MCP tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog
import yaml

from src.mcp.protocol import MCPToolDefinition

if TYPE_CHECKING:
    from pathlib import Path

logger = structlog.get_logger(__name__)


@dataclass
class SkillParameter:
    """A parameter for a skill."""

    name: str
    type: str
    description: str
    required: bool = True
    default: Any = None


@dataclass
class Skill:
    """A skill that can be distributed via MCP.

    Skills are the atomic units of capability in ProtoForge.
    Each skill maps to an MCP tool and is handled by a specific agent.
    """

    name: str
    description: str
    agent_type: str
    version: str = "1.0.0"
    parameters: list[SkillParameter] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    examples: list[dict[str, str]] = field(default_factory=list)

    def to_mcp_tool(self) -> MCPToolDefinition:
        """Convert this skill to an MCP tool definition."""
        properties: dict[str, Any] = {}
        required: list[str] = []

        for param in self.parameters:
            properties[param.name] = {
                "type": param.type,
                "description": param.description,
            }
            if param.default is not None:
                properties[param.name]["default"] = param.default
            if param.required:
                required.append(param.name)

        input_schema = {
            "type": "object",
            "properties": properties,
            "required": required,
        }

        return MCPToolDefinition(
            name=self.name,
            description=self.description,
            input_schema=input_schema,
            agent_type=self.agent_type,
        )


class SkillLoader:
    """Loads skills from YAML definition files."""

    @staticmethod
    def load_from_file(path: Path) -> Skill:
        """Load a single skill from a YAML file."""
        with open(path) as f:
            data = yaml.safe_load(f)

        params = [
            SkillParameter(
                name=p["name"],
                type=p.get("type", "string"),
                description=p.get("description", ""),
                required=p.get("required", True),
                default=p.get("default"),
            )
            for p in data.get("parameters", [])
        ]

        return Skill(
            name=data["name"],
            description=data["description"],
            agent_type=data["agent_type"],
            version=data.get("version", "1.0.0"),
            parameters=params,
            tags=data.get("tags", []),
            examples=data.get("examples", []),
        )

    @staticmethod
    def load_from_directory(directory: Path) -> list[Skill]:
        """Load all skills from YAML files in a directory."""
        skills: list[Skill] = []
        if not directory.exists():
            logger.warning("skills_directory_not_found", path=str(directory))
            return skills

        for yaml_file in sorted(directory.glob("*.yaml")):
            try:
                skill = SkillLoader.load_from_file(yaml_file)
                skills.append(skill)
                logger.info("skill_loaded", name=skill.name, agent=skill.agent_type)
            except Exception as exc:
                logger.error("skill_load_failed", file=str(yaml_file), error=str(exc))

        return skills
