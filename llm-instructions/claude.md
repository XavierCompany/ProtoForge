# ProtoForge — Claude Code Instructions

> Full content variant for Claude Code (`claude code`).
> Canonical source: `.github/copilot-instructions.md`. If they diverge, that file wins.
> The repo-root `CLAUDE.md` is a thin pointer that redirects here.

## What is ProtoForge?

A **plan-first multi-agent orchestrator** built on the Microsoft Agent Framework
(Python). Every user request flows through a Plan Agent before any specialist
executes. Human-in-the-Loop (HITL) gates at every decision point. Context window
governance enforces a 128 K token hard cap per orchestration run.

## Data Flow (happy path)

```
User → FastAPI /chat → IntentRouter (keyword + LLM)
  → Plan Agent (HITL review)
    → Sub-Plan Agent (HITL review)
      → Fan-out to ≤3 specialist agents in parallel
        → Aggregate response → User
```

## Key Abstractions

| Abstraction | Location | Purpose |
|---|---|---|
| `BaseAgent` | `src/agents/base.py` | ABC — all agents implement `execute()` |
| `OrchestratorEngine` | `src/orchestrator/engine.py` | Top-level pipeline coordinator |
| `IntentRouter` | `src/orchestrator/router.py` | Keyword + LLM-based intent classification |
| `GovernanceGuardian` | `src/governance/guardian.py` | Token cap enforce + HITL alerts |
| `ForgeLoader` | `src/forge/loader.py` | Reads `forge/` YAML at startup |
| `ContextBudgetManager` | `src/forge/context_budget.py` | Per-agent token allocation |
| `ConversationContext` | `src/orchestrator/context.py` | Shared state across a run |
| `PlanSelector` | `src/orchestrator/plan_selector.py` | Plan HITL gate |
| `MCPServer` | `src/mcp/server.py` | MCP protocol implementation |

## Coding Conventions

- **Python 3.12+**, type hints everywhere, `from __future__ import annotations`
- **Pydantic v2** for settings (`BaseSettings`), dataclasses for domain models
- **structlog** for structured logging — `logger = structlog.get_logger(__name__)`
- **Async**: All agent `execute()` methods are `async def`
- **Token math**: `plan(32K) + sub-plan(20K) + 3×specialist(≤25K) ≤ 128K`
- **Tests**: pytest + pytest-asyncio, 421 tests, fixtures in `tests/conftest.py`
- **Lint**: ruff (check + format), mypy for type checking

## Documentation — Read ALL in order

1. `.github/copilot-instructions.md` — orientation & conventions (~140 lines)
2. `ARCHITECTURE.md` — compact architecture, APIs, module graph (~255 lines)
3. `SOURCE_OF_TRUTH.md` — canonical ownership map (~195 lines)
4. `MAINTENANCE.md` — update protocol, anti-drift rules (~455 lines)
5. `TODO.md` — prioritised backlog P0→P3 (~240 lines)
6. `CHANGELOG.md` — version history (~140 lines)
7. `README.md` — onboarding, full endpoint table, quick-start (~820 lines)
8. `GUIDE.md` — deep-dive reference, 19 sections (~2760 lines)
9. `GUIDE2.md` — maintenance & tuning guide, 13 sections (~905 lines)
10. `BUILDING_AGENTS.md` — practical tutorial: build a new agent with AI Foundry (~195 lines)

## What NOT to Change Without Care

- `forge/_context_window.yaml` budget math — recalculate sum if any value changes
- `AgentType` enum in `router.py` — must match `forge/agents/*/agent.yaml` IDs
- HITL timeout/auto-resolve semantics — fail-closed for lifecycle, fail-open for plans
- `GovernanceGuardian.enforce_hard_cap` — disabling removes safety net

## Version

Current: `0.1.1` — defined in `pyproject.toml` (canonical).
