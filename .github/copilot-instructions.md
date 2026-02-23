# ProtoForge — Copilot Instructions

> **Read this file FIRST.** It is optimised for LLM context windows (~200 lines).
> For full details see ARCHITECTURE.md, then SOURCE_OF_TRUTH.md.

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

Enriched flow (`/chat/enriched`) prepends WorkIQ M365 context query + 2 HITL
phases before routing.

## Directory Map

```
src/
  config.py            # Pydantic Settings (LLM, Server, MCP, Forge, Otel)
  main.py              # CLI entry, bootstrap, agent registration
  server.py            # FastAPI HTTP app — 35+ endpoints + HTML dashboard
  agents/
    base.py            # ABC for all agents (from_manifest or explicit)
    generic.py         # Default no-op agent implementation
    *_agent.py         # Specialist implementations (9 agents)
  forge/
    loader.py          # Reads forge/ YAML ecosystem at startup
    context_budget.py  # Per-agent token budget allocation + truncation
    contributions.py   # CRUD for dynamic skill/prompt/workflow additions
  governance/
    guardian.py        # Context window cap + skill cap enforcement + HITL
    selector.py        # Agent lifecycle HITL (disable/enable/unregister)
  orchestrator/
    engine.py          # Core pipeline: route→plan→sub-plan→fan-out→aggregate
    router.py          # IntentRouter — keyword patterns + LLM classification
    context.py         # ConversationContext + AgentResult dataclasses
    plan_selector.py   # Plan HITL gate
  mcp/
    server.py          # MCP JSON-RPC server (tools, resources, prompts)
    protocol.py        # MCP message types / schema definitions
    skills.py          # Skill discovery + loading from forge/ YAML
  registry/
    catalog.py         # Agent catalog — list, search, metadata
    workflows.py       # Workflow bundling + execution
  workiq/
    client.py          # WorkIQ CLI wrapper (M365 context)
    selector.py        # WorkIQ 2-phase HITL selector
forge/
  _registry.yaml       # Agent registry — single source of truth for IDs
  _context_window.yaml # Token budget config (128K cap, per-agent limits)
  agents/*/agent.yaml  # Per-agent manifests (id, description, skills, budget)
  plan/                # Plan Agent coordination rules + prompts
  shared/              # Shared prompts and workflows
tests/
  conftest.py          # Shared fixtures (engine, agents, guardian, router)
  test_*.py            # 363 tests — one file per domain
```

## Key Abstractions

| Abstraction | Location | Purpose |
|---|---|---|
| `BaseAgent` | `src/agents/base.py` | ABC — all agents implement `execute()` |
| `OrchestratorEngine` | `src/orchestrator/engine.py` | Top-level pipeline coordinator |
| `IntentRouter` | `src/orchestrator/router.py` | Keyword + LLM-based intent classification |
| `GovernanceGuardian` | `src/governance/guardian.py` | Token cap enforce + HITL alerts |
| `GovernanceSelector` | `src/governance/selector.py` | Agent lifecycle HITL reviews |
| `ForgeLoader` | `src/forge/loader.py` | Reads `forge/` YAML at startup |
| `ContextBudgetManager` | `src/forge/context_budget.py` | Per-agent token allocation |
| `ConversationContext` | `src/orchestrator/context.py` | Shared state across a run |
| `PlanSelector` | `src/orchestrator/plan_selector.py` | Plan HITL gate |
| `MCPServer` | `src/mcp/server.py` | MCP protocol implementation |

## Coding Conventions

- **Python 3.12+**, type hints everywhere, `from __future__ import annotations`
- **Pydantic v2** for settings (`BaseSettings`), dataclasses for domain models
- **structlog** for structured logging — `logger = structlog.get_logger(__name__)`
- **HITL pattern**: prepare review → expose via HTTP → wait with timeout → resolve
  - Timeout default: 120s → auto-resolve (fail-closed for lifecycle, fail-open for plans)
- **Token math constraint**: `plan(32K) + sub-plan(20K) + 3×specialist(≤25K) ≤ 128K`
- **Tests**: pytest + pytest-asyncio, fixtures in `conftest.py`, 363 tests
- **Lint**: ruff (check + format), mypy for type checking
- **Async**: All agent `execute()` methods are `async def`

## HITL Gates (5 types)

1. **Plan HITL** — user reviews Plan Agent suggestions + keyword routing
2. **Sub-Plan HITL** — user reviews resource plan + optional brief override
3. **Governance Context HITL** — triggered at 110K tokens, proposes decomposition
4. **Governance Skill Cap HITL** — triggered when agent has >4 skills
5. **Lifecycle HITL** — triggered on agent disable/remove requests

## What NOT to Change Without Care

- `forge/_context_window.yaml` budget math — recalculate sum if any value changes
- `AgentType` enum in `router.py` — must match `forge/agents/*/agent.yaml` IDs
- HITL timeout/auto-resolve semantics — fail-closed for lifecycle, fail-open for plans
- `GovernanceGuardian.enforce_hard_cap` — disabling removes safety net

## Canonical Sources (drift risk)

Agent identity defined in 4 places — canonical is `forge/agents/<id>/agent.yaml`.
See SOURCE_OF_TRUTH.md for the full ownership map.

## Documentation Reading Order

Read **ALL** documentation files in the order below. Each contains unique
information not duplicated elsewhere. Total: ~5400 lines (~18 K tokens) — fits
easily in modern context windows.

1. **This file** (copilot-instructions.md) — orientation & conventions (~140 lines)
2. **ARCHITECTURE.md** — compact architecture, APIs, module graph (~260 lines)
3. **SOURCE_OF_TRUTH.md** — canonical ownership map (~190 lines)
4. **MAINTENANCE.md** — update protocol, anti-drift rules (~450 lines)
5. **TODO.md** — prioritised backlog P0→P3 (~240 lines)
6. **GUIDE.md** — deep-dive reference, 19 sections (~2750 lines, use ARCHITECTURE.md §10 section index for navigation)
7. **GUIDE2.md** — maintenance & tuning guide, 13 sections (~940 lines)
8. **README.md** — onboarding, full endpoint table, quick-start (~810 lines)
9. **CHANGELOG.md** — version history (~100 lines)

## Version

Current: `0.1.1` — defined in `pyproject.toml` (canonical), mirrored in
`src/server.py`, `src/mcp/server.py`, `CHANGELOG.md`, `MAINTENANCE.md`,
`TODO.md`, `SOURCE_OF_TRUTH.md`.
