"""Automated validation for .github/ Copilot customization files.

Catches drift between customization files and the live codebase:
- Agent identity consistency (4 locations match)
- Token budget math (hard cap not exceeded)
- Customization file references (files mentioned actually exist)
- Skill/prompt/agent model defaults (Claude Opus 4.6)
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent


# ── Helpers ───────────────────────────────────────────────────────────────


def _read_yaml(path: Path) -> dict:
    """Load a YAML file, returning its parsed content."""
    with open(path) as f:
        return yaml.safe_load(f)


def _parse_frontmatter(path: Path) -> dict:
    """Extract YAML frontmatter from a markdown file (between --- fences)."""
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\s*\n(.*?\n)---\s*\n", text, re.DOTALL)
    if not match:
        return {}
    return yaml.safe_load(match.group(1)) or {}


def _collect_customization_files() -> list[Path]:
    """Return all .md files in .github/{agents,prompts,skills}."""
    dirs = [
        ROOT / ".github" / "agents",
        ROOT / ".github" / "prompts",
        ROOT / ".github" / "skills",
    ]
    files: list[Path] = []
    for d in dirs:
        if d.exists():
            files.extend(d.rglob("*.md"))
    return files


# ── Budget Math ───────────────────────────────────────────────────────────


def _gather_budgets() -> tuple[int, int, dict[str, int]]:
    """Return (hard_cap, max_parallel, {agent_id: total_budget}).

    Total budget per agent = max_input_tokens + max_output_tokens.
    """
    ctx = _read_yaml(ROOT / "forge" / "_context_window.yaml")
    hard_cap = ctx["global"]["max_total_tokens"]
    max_parallel = ctx["scaling"]["max_parallel_agents"]

    budgets: dict[str, int] = {}

    # Plan agent
    plan_yaml = ROOT / "forge" / "plan" / "agent.yaml"
    plan = _read_yaml(plan_yaml)
    inp = plan["context_budget"]["max_input_tokens"]
    out = plan["context_budget"]["max_output_tokens"]
    budgets["plan"] = inp + out

    # All agents under forge/agents/
    agents_dir = ROOT / "forge" / "agents"
    for agent_dir in sorted(agents_dir.iterdir()):
        manifest = agent_dir / "agent.yaml"
        if manifest.exists():
            data = _read_yaml(manifest)
            agent_id = data["id"]
            inp = data["context_budget"]["max_input_tokens"]
            out = data["context_budget"]["max_output_tokens"]
            budgets[agent_id] = inp + out

    return hard_cap, max_parallel, budgets


class TestBudgetMath:
    """Verify the 128K token cap is not exceeded."""

    def test_worst_case_within_cap(self) -> None:
        hard_cap, max_parallel, budgets = _gather_budgets()

        plan_budget = budgets.pop("plan")
        sub_plan_budget = budgets.pop("sub_plan")

        # Top N specialist budgets (sorted descending)
        sorted_specialist = sorted(budgets.values(), reverse=True)
        top_n = sorted_specialist[:max_parallel]

        worst_case = plan_budget + sub_plan_budget + sum(top_n)
        headroom = hard_cap - worst_case

        assert worst_case <= hard_cap, (
            f"Budget violation: {worst_case:,} > {hard_cap:,} "
            f"(plan={plan_budget:,} + sub_plan={sub_plan_budget:,} + "
            f"top {max_parallel}={top_n})"
        )
        # Warn on tight headroom
        assert headroom >= 0, f"Negative headroom: {headroom:,}"

    def test_every_agent_has_budget(self) -> None:
        """Every registered agent YAML must define context_budget."""
        agents_dir = ROOT / "forge" / "agents"
        for agent_dir in sorted(agents_dir.iterdir()):
            manifest = agent_dir / "agent.yaml"
            if manifest.exists():
                data = _read_yaml(manifest)
                assert "context_budget" in data, (
                    f"{manifest.relative_to(ROOT)} missing context_budget"
                )
                cb = data["context_budget"]
                assert "max_input_tokens" in cb, (
                    f"{manifest.relative_to(ROOT)} missing max_input_tokens"
                )
                assert "max_output_tokens" in cb, (
                    f"{manifest.relative_to(ROOT)} missing max_output_tokens"
                )


# ── Agent Identity Consistency ────────────────────────────────────────────


class TestAgentIdentity:
    """Verify each agent exists in all 4 required locations."""

    def test_registry_matches_yaml_manifests(self) -> None:
        """Agent IDs in _registry.yaml match forge/agents/*/agent.yaml."""
        registry = _read_yaml(ROOT / "forge" / "_registry.yaml")
        registry_ids = set(registry.get("agents", {}).keys())

        yaml_ids: set[str] = set()
        agents_dir = ROOT / "forge" / "agents"
        for agent_dir in sorted(agents_dir.iterdir()):
            manifest = agent_dir / "agent.yaml"
            if manifest.exists():
                data = _read_yaml(manifest)
                aid = data["id"]
                if aid != "sub_plan":  # sub_plan is under agents/ but is coordinator-level
                    yaml_ids.add(aid)

        # sub_plan is special — it may or may not be in the agents section
        registry_ids_no_sub = registry_ids - {"sub_plan"}
        yaml_ids_no_sub = yaml_ids - {"sub_plan"}
        assert registry_ids_no_sub == yaml_ids_no_sub, (
            f"Registry/YAML mismatch: "
            f"in registry only: {registry_ids_no_sub - yaml_ids_no_sub}, "
            f"in YAML only: {yaml_ids_no_sub - registry_ids_no_sub}"
        )

    def test_router_enum_covers_registry(self) -> None:
        """Every registry agent has a matching AgentType enum value in router.py."""
        registry = _read_yaml(ROOT / "forge" / "_registry.yaml")
        registry_ids = set(registry.get("agents", {}).keys())
        # Also include plan from coordinator section
        if "plan" in registry.get("coordinator", {}):
            registry_ids.add("plan")

        router_path = ROOT / "src" / "orchestrator" / "router.py"
        router_text = router_path.read_text(encoding="utf-8")

        # Extract enum values: AGENT_ID = "agent_id"
        enum_values = set(re.findall(r'=\s*"(\w+)"', router_text))

        for agent_id in registry_ids:
            assert agent_id in enum_values, (
                f"Agent '{agent_id}' in _registry.yaml but missing "
                f"from AgentType enum in router.py"
            )

    def test_agent_python_files_exist(self) -> None:
        """Every non-generic agent has a Python implementation file."""
        agents_dir = ROOT / "forge" / "agents"
        # Agents that use GenericAgent (no dedicated file)
        generic_agents = {"code_research", "data_analysis"}

        for agent_dir in sorted(agents_dir.iterdir()):
            manifest = agent_dir / "agent.yaml"
            if manifest.exists():
                data = _read_yaml(manifest)
                aid = data["id"]
                if aid in generic_agents:
                    continue
                py_file = ROOT / "src" / "agents" / f"{aid}_agent.py"
                assert py_file.exists(), (
                    f"Agent '{aid}' has YAML manifest but no Python file "
                    f"at {py_file.relative_to(ROOT)}"
                )


# ── Customization File Validation ─────────────────────────────────────────


class TestCustomizationFiles:
    """Validate .github/ customization files don't reference phantom paths."""

    def test_all_customization_files_have_frontmatter(self) -> None:
        """Every customization file must have YAML frontmatter."""
        for path in _collect_customization_files():
            fm = _parse_frontmatter(path)
            assert fm, (
                f"{path.relative_to(ROOT)} missing YAML frontmatter"
            )

    def test_agents_have_model_field(self) -> None:
        """Custom agents must specify model: 'Claude Opus 4.6'."""
        agents_dir = ROOT / ".github" / "agents"
        if not agents_dir.exists():
            pytest.skip("No .github/agents/ directory")
        for path in agents_dir.glob("*.md"):
            fm = _parse_frontmatter(path)
            assert fm.get("model") == "Claude Opus 4.6", (
                f"{path.relative_to(ROOT)} must set model: 'Claude Opus 4.6', "
                f"got: {fm.get('model')!r}"
            )

    def test_prompts_have_model_field(self) -> None:
        """Prompt files must specify model: 'Claude Opus 4.6'."""
        prompts_dir = ROOT / ".github" / "prompts"
        if not prompts_dir.exists():
            pytest.skip("No .github/prompts/ directory")
        for path in prompts_dir.glob("*.prompt.md"):
            fm = _parse_frontmatter(path)
            assert fm.get("model") == "Claude Opus 4.6", (
                f"{path.relative_to(ROOT)} must set model: 'Claude Opus 4.6', "
                f"got: {fm.get('model')!r}"
            )

    def test_skills_have_required_frontmatter(self) -> None:
        """Skills must have name and description in frontmatter."""
        skills_dir = ROOT / ".github" / "skills"
        if not skills_dir.exists():
            pytest.skip("No .github/skills/ directory")
        for path in skills_dir.rglob("SKILL.md"):
            fm = _parse_frontmatter(path)
            assert "name" in fm, (
                f"{path.relative_to(ROOT)} missing 'name' in frontmatter"
            )
            assert "description" in fm, (
                f"{path.relative_to(ROOT)} missing 'description' in frontmatter"
            )

    def test_no_hardcoded_budget_values_in_skills(self) -> None:
        """Skills should not contain hardcoded budget numbers (drift risk).

        They should instruct the reader to read from YAML files instead.
        """
        skills_dir = ROOT / ".github" / "skills"
        if not skills_dir.exists():
            pytest.skip("No .github/skills/ directory")
        # These specific numbers would indicate hardcoded budget values
        hardcoded_pattern = re.compile(
            r"\b(128[,.]?000|124[,.]?000)\b"
        )
        for path in skills_dir.rglob("SKILL.md"):
            text = path.read_text(encoding="utf-8")
            matches = hardcoded_pattern.findall(text)
            assert not matches, (
                f"{path.relative_to(ROOT)} contains hardcoded budget values "
                f"{matches} — use 'read from YAML' instructions instead"
            )

    def test_referenced_source_files_exist(self) -> None:
        """Key source files referenced in customization content exist."""
        key_refs = [
            "src/agents/base.py",
            "src/orchestrator/router.py",
            "src/governance/guardian.py",
            "src/governance/selector.py",
            "forge/_context_window.yaml",
            "forge/_registry.yaml",
            "SOURCE_OF_TRUTH.md",
            ".github/copilot-instructions.md",
            "MAINTENANCE.md",
            "tests/conftest.py",
        ]
        for ref in key_refs:
            path = ROOT / ref
            assert path.exists(), (
                f"Customization files reference '{ref}' but it does not exist"
            )
