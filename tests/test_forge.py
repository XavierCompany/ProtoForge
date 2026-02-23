"""Tests for the forge loader, context budget manager, and contribution manager."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml

from src.forge.context_budget import ContextBudgetManager
from src.forge.contributions import ContributionManager, ValidationError
from src.forge.loader import ForgeLoader

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def forge_dir(tmp_path: Path) -> Path:
    """Create a minimal forge/ directory structure for testing."""
    forge = tmp_path / "forge"

    # Plan (coordinator)
    plan = forge / "plan"
    (plan / "prompts").mkdir(parents=True)
    (plan / "skills").mkdir(parents=True)
    (plan / "instructions").mkdir(parents=True)
    (plan / "workflows").mkdir(parents=True)

    (plan / "agent.yaml").write_text(yaml.dump({
        "id": "plan",
        "name": "Plan Agent",
        "type": "coordinator",
        "version": "1.0.0",
        "description": "Coordinator",
        "context_budget": {"max_input_tokens": 24000, "max_output_tokens": 12000, "strategy": "priority"},
        "skills": ["skills/plan_task.yaml"],
        "prompts": ["prompts/system.md"],
        "instructions": ["instructions/routing_rules.md"],
        "subagents": ["log_analysis", "code_research"],
        "tags": ["plan"],
    }))
    (plan / "prompts" / "system.md").write_text("# Plan Agent System Prompt\nYou are the Plan Agent.")
    (plan / "instructions" / "routing_rules.md").write_text("# Routing Rules\nRoute wisely.")
    (plan / "skills" / "plan_task.yaml").write_text(yaml.dump({
        "name": "plan_task",
        "description": "Create a plan",
        "agent_type": "plan",
        "parameters": [{"name": "message", "type": "string", "description": "User message", "required": True}],
    }))
    (plan / "workflows" / "plan_and_execute.yaml").write_text(yaml.dump({
        "name": "plan_and_execute",
        "steps": [{"name": "plan", "agent_type": "plan"}],
    }))

    # Specialist agent
    log = forge / "agents" / "log_analysis"
    (log / "prompts").mkdir(parents=True)
    (log / "skills").mkdir(parents=True)
    (log / "instructions").mkdir(parents=True)

    (log / "agent.yaml").write_text(yaml.dump({
        "id": "log_analysis",
        "name": "Log Analysis Agent",
        "type": "specialist",
        "version": "1.0.0",
        "description": "Analyze logs",
        "skills": ["skills/analyze_logs.yaml"],
        "prompts": ["prompts/system.md"],
        "instructions": ["instructions/log_formats.md"],
        "tags": ["logs"],
    }))
    (log / "prompts" / "system.md").write_text("# Log Analysis System Prompt")
    (log / "instructions" / "log_formats.md").write_text("# Log Formats")
    (log / "skills" / "analyze_logs.yaml").write_text(yaml.dump({
        "name": "analyze_logs",
        "description": "Analyze logs",
        "agent_type": "log_analysis",
        "parameters": [{"name": "logs", "type": "string", "description": "Log data", "required": True}],
    }))

    # Shared
    shared = forge / "shared"
    (shared / "prompts").mkdir(parents=True)
    (shared / "instructions").mkdir(parents=True)
    (shared / "workflows").mkdir(parents=True)

    (shared / "prompts" / "error_handling.md").write_text("# Error Handling")
    (shared / "instructions" / "quality_standards.md").write_text("# Quality Standards")
    (shared / "workflows" / "code_review.yaml").write_text(yaml.dump({
        "name": "code_review",
        "steps": [{"name": "scan", "agent_type": "security_sentinel"}],
    }))

    # Context window config
    (forge / "_context_window.yaml").write_text(yaml.dump({
        "version": "1.0.0",
        "global": {"max_total_tokens": 128000},
        "defaults": {
            "specialist": {"max_input_tokens": 16000, "max_output_tokens": 8000, "strategy": "priority"},
            "coordinator": {"max_input_tokens": 24000, "max_output_tokens": 12000, "strategy": "priority"},
        },
        "token_counting": {"method": "character_estimate"},
        "strategies": {"sliding_window": {"overlap_tokens": 200}},
    }))

    # Contrib (empty scaffolding)
    (forge / "contrib" / "agents").mkdir(parents=True)
    (forge / "contrib" / "skills").mkdir(parents=True)
    (forge / "contrib" / "workflows").mkdir(parents=True)
    (forge / "contrib" / "audit_log.yaml").write_text(yaml.dump({"version": "1.0.0", "entries": []}))

    return forge


# ---------------------------------------------------------------------------
# ForgeLoader tests
# ---------------------------------------------------------------------------

class TestForgeLoader:
    def test_load_discovers_coordinator(self, forge_dir: Path) -> None:
        loader = ForgeLoader(forge_dir)
        registry = loader.load()
        assert registry.coordinator is not None
        assert registry.coordinator.id == "plan"
        assert registry.coordinator.type == "coordinator"

    def test_load_discovers_agents(self, forge_dir: Path) -> None:
        loader = ForgeLoader(forge_dir)
        registry = loader.load()
        assert "log_analysis" in registry.agents
        assert registry.agents["log_analysis"].type == "specialist"

    def test_load_resolves_prompts(self, forge_dir: Path) -> None:
        loader = ForgeLoader(forge_dir)
        registry = loader.load()
        assert "system" in registry.coordinator.resolved_prompts
        assert "Plan Agent" in registry.coordinator.resolved_prompts["system"]

    def test_load_resolves_instructions(self, forge_dir: Path) -> None:
        loader = ForgeLoader(forge_dir)
        registry = loader.load()
        assert "routing_rules" in registry.coordinator.resolved_instructions

    def test_load_collects_skills(self, forge_dir: Path) -> None:
        loader = ForgeLoader(forge_dir)
        registry = loader.load()
        skill_names = {s["name"] for s in registry.skills}
        assert "plan_task" in skill_names
        assert "analyze_logs" in skill_names

    def test_load_collects_workflows(self, forge_dir: Path) -> None:
        loader = ForgeLoader(forge_dir)
        registry = loader.load()
        wf_names = {w["name"] for w in registry.workflows}
        assert "plan_and_execute" in wf_names
        assert "code_review" in wf_names

    def test_load_shared_prompts(self, forge_dir: Path) -> None:
        loader = ForgeLoader(forge_dir)
        registry = loader.load()
        assert "error_handling" in registry.shared_prompts

    def test_load_shared_instructions(self, forge_dir: Path) -> None:
        loader = ForgeLoader(forge_dir)
        registry = loader.load()
        assert "quality_standards" in registry.shared_instructions

    def test_load_context_config(self, forge_dir: Path) -> None:
        loader = ForgeLoader(forge_dir)
        registry = loader.load()
        assert registry.context_config.get("global", {}).get("max_total_tokens") == 128000

    def test_list_agent_ids(self, forge_dir: Path) -> None:
        loader = ForgeLoader(forge_dir)
        loader.load()
        ids = loader.list_agent_ids()
        assert "plan" in ids
        assert "log_analysis" in ids

    def test_get_agent(self, forge_dir: Path) -> None:
        loader = ForgeLoader(forge_dir)
        loader.load()
        assert loader.get_agent("plan") is not None
        assert loader.get_agent("log_analysis") is not None
        assert loader.get_agent("nonexistent") is None

    def test_nonexistent_forge_root(self, tmp_path: Path) -> None:
        loader = ForgeLoader(tmp_path / "nonexistent")
        registry = loader.load()
        assert registry.coordinator is None
        assert len(registry.agents) == 0

    def test_load_contrib_agent(self, forge_dir: Path) -> None:
        """Test that contributed agents are discovered."""
        contrib_agent = forge_dir / "contrib" / "agents" / "custom_agent"
        (contrib_agent / "prompts").mkdir(parents=True)
        (contrib_agent / "agent.yaml").write_text(yaml.dump({
            "id": "custom_agent",
            "name": "Custom Agent",
            "type": "specialist",
            "version": "1.0.0",
            "description": "A custom contributed agent",
        }))

        loader = ForgeLoader(forge_dir)
        registry = loader.load()
        assert "custom_agent" in registry.agents


# ---------------------------------------------------------------------------
# ContextBudgetManager tests
# ---------------------------------------------------------------------------

class TestContextBudgetManager:
    def test_allocate_with_defaults(self) -> None:
        config = {
            "defaults": {
                "specialist": {"max_input_tokens": 16000, "max_output_tokens": 8000, "strategy": "priority"},
            },
        }
        mgr = ContextBudgetManager(config)
        budget = mgr.allocate("test_agent", "specialist")
        assert budget.max_input == 16000
        assert budget.max_output == 8000
        assert budget.strategy == "priority"

    def test_allocate_with_override(self) -> None:
        mgr = ContextBudgetManager()
        override = {"max_input_tokens": 10000, "max_output_tokens": 5000, "strategy": "sliding_window"}
        budget = mgr.allocate("test", override=override)
        assert budget.max_input == 10000
        assert budget.strategy == "sliding_window"

    def test_count_tokens_character_estimate(self) -> None:
        mgr = ContextBudgetManager({"token_counting": {"method": "character_estimate"}})
        assert mgr.count_tokens("hello world") > 0

    def test_fits_budget(self) -> None:
        mgr = ContextBudgetManager()
        mgr.allocate("a", override={"max_input_tokens": 10, "max_output_tokens": 5})
        # Short text should fit
        assert mgr.fits_budget("a", "hi")
        # Long text should not
        assert not mgr.fits_budget("a", "x" * 200)

    def test_record_usage_and_remaining(self) -> None:
        mgr = ContextBudgetManager()
        mgr.allocate("a", override={"max_input_tokens": 100, "max_output_tokens": 50})
        mgr.record_usage("a", 30, "input")
        assert mgr.remaining("a", "input") == 70

    def test_truncate_priority(self) -> None:
        mgr = ContextBudgetManager()
        mgr.allocate("a", override={"max_input_tokens": 5, "max_output_tokens": 5, "strategy": "priority"})
        result = mgr.truncate("a", "x" * 200)
        assert len(result) < 200

    def test_truncate_sliding_window(self) -> None:
        config = {"strategies": {"sliding_window": {"overlap_tokens": 2}}}
        mgr = ContextBudgetManager(config)
        mgr.allocate("a", override={"max_input_tokens": 5, "max_output_tokens": 5, "strategy": "sliding_window"})
        result = mgr.truncate("a", "x" * 200)
        assert len(result) < 200

    def test_usage_report(self) -> None:
        mgr = ContextBudgetManager()
        mgr.allocate("a", override={"max_input_tokens": 100, "max_output_tokens": 50})
        mgr.record_usage("a", 20, "input")
        report = mgr.usage_report()
        assert "a" in report
        assert report["a"]["used"]["input"] == 20
        assert report["a"]["remaining"]["input"] == 80

    def test_no_budget_unlimited(self) -> None:
        mgr = ContextBudgetManager()
        assert mgr.fits_budget("unknown", "anything")
        assert mgr.remaining("unknown") == 999_999


# ---------------------------------------------------------------------------
# ContributionManager tests
# ---------------------------------------------------------------------------

class TestContributionManager:
    def test_create_agent(self, forge_dir: Path) -> None:
        mgr = ContributionManager(forge_dir)
        path = mgr.create_agent(
            "my_agent",
            {"id": "my_agent", "name": "My Agent", "type": "specialist", "description": "Custom"},
            "# System prompt",
            author="test",
        )
        assert path.exists()
        assert (path / "agent.yaml").exists()
        assert (path / "prompts" / "system.md").exists()

    def test_create_agent_duplicate_fails(self, forge_dir: Path) -> None:
        mgr = ContributionManager(forge_dir)
        mgr.create_agent("dup", {"id": "dup", "name": "D", "type": "s", "description": "D"}, "# Prompt")
        with pytest.raises(FileExistsError):
            mgr.create_agent("dup", {"id": "dup", "name": "D", "type": "s", "description": "D"}, "# Prompt")

    def test_create_agent_missing_fields(self, forge_dir: Path) -> None:
        mgr = ContributionManager(forge_dir)
        with pytest.raises(ValidationError):
            mgr.create_agent("bad", {"id": "bad"}, "# Prompt")

    def test_update_agent(self, forge_dir: Path) -> None:
        mgr = ContributionManager(forge_dir)
        mgr.create_agent("upd", {"id": "upd", "name": "U", "type": "s", "description": "D"}, "# Prompt")
        mgr.update_agent("upd", system_prompt="# Updated prompt", author="tester")
        content = (forge_dir / "contrib" / "agents" / "upd" / "prompts" / "system.md").read_text()
        assert "Updated" in content

    def test_delete_agent(self, forge_dir: Path) -> None:
        mgr = ContributionManager(forge_dir)
        mgr.create_agent("del", {"id": "del", "name": "D", "type": "s", "description": "D"}, "# X")
        mgr.delete_agent("del", author="test")
        assert not (forge_dir / "contrib" / "agents" / "del").exists()

    def test_create_skill(self, forge_dir: Path) -> None:
        mgr = ContributionManager(forge_dir)
        path = mgr.create_skill({"name": "my_skill", "description": "A skill", "parameters": []})
        assert path.exists()

    def test_create_skill_missing_fields(self, forge_dir: Path) -> None:
        mgr = ContributionManager(forge_dir)
        with pytest.raises(ValidationError):
            mgr.create_skill({"name": "incomplete"})

    def test_delete_skill(self, forge_dir: Path) -> None:
        mgr = ContributionManager(forge_dir)
        mgr.create_skill({"name": "to_del", "description": "D", "parameters": []})
        mgr.delete_skill("to_del")
        assert not (forge_dir / "contrib" / "skills" / "to_del.yaml").exists()

    def test_create_workflow(self, forge_dir: Path) -> None:
        mgr = ContributionManager(forge_dir)
        path = mgr.create_workflow({"name": "my_wf", "steps": [{"name": "s1", "agent_type": "plan"}]})
        assert path.exists()

    def test_delete_workflow(self, forge_dir: Path) -> None:
        mgr = ContributionManager(forge_dir)
        mgr.create_workflow({"name": "wf_del", "steps": [{"name": "s1", "agent_type": "plan"}]})
        mgr.delete_workflow("wf_del")
        assert not (forge_dir / "contrib" / "workflows" / "wf_del.yaml").exists()

    def test_list_contributions(self, forge_dir: Path) -> None:
        mgr = ContributionManager(forge_dir)
        mgr.create_agent("a1", {"id": "a1", "name": "A", "type": "s", "description": "D"}, "# P")
        mgr.create_skill({"name": "s1", "description": "D", "parameters": []})
        result = mgr.list_contributions()
        assert "a1" in result["agents"]
        assert "s1" in result["skills"]

    def test_audit_log(self, forge_dir: Path) -> None:
        mgr = ContributionManager(forge_dir)
        mgr.create_agent("aud", {"id": "aud", "name": "A", "type": "s", "description": "D"}, "# P", author="alice")
        log = mgr.get_audit_log()
        assert len(log) >= 1
        assert log[-1]["author"] == "alice"
        assert log[-1]["action"] == "create_agent"
