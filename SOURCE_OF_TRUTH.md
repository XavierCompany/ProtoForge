# Source of Truth — ProtoForge Canonical Ownership Map

> **TL;DR for LLMs**: Canonical ownership map (190+ lines / 9 sections).
> Answers: “where is X defined, and what copies must I update?”
> Covers: agent identity, token budgets, routing, prompts, config, governance, tests.
>
> This is doc **3 of 9** in the reading order. Read
> [ARCHITECTURE.md](ARCHITECTURE.md) first for orientation.

> **Purpose**: When the same concept is defined in multiple places, this file
> declares which source is **canonical** and which are **derived/duplicated**.
>
> If you change a canonical source, update its derived copies. If a derived
> copy drifts, the canonical source wins.

---

## 1. Agent Identity

An agent's identity (ID, name, description, type) is defined in multiple places.
This table shows which source is canonical for each attribute.

| Attribute | Canonical Source | Derived / Duplicated In |
|-----------|-----------------|------------------------|
| Agent ID | `forge/agents/<id>/agent.yaml` → `id:` | `AgentType` enum in `src/orchestrator/router.py`, `_SPECIALISED_CLASSES` in `src/main.py`, `_registry.yaml` |
| Agent name | `forge/agents/<id>/agent.yaml` → `name:` | `_default_agents` dict in `src/main.py` |
| Agent description | `forge/agents/<id>/agent.yaml` → `description:` | `get_llm_routing_prompt()` in `router.py`, `_default_agents` in `main.py` |
| Agent type (coordinator/specialist) | `forge/agents/<id>/agent.yaml` → `type:` | `_registry.yaml` |
| Python class binding | `_SPECIALISED_CLASSES` dict in `src/main.py` | *(should move to `agent.yaml` → `python_class:` — see TODO P2-14)* |

### Drift risk

The `AgentType` StrEnum in `router.py` must match the set of agent IDs in
`forge/agents/*/agent.yaml`. If you add a new agent directory without adding
an enum member, keyword routing won't have patterns for it (but dynamic
routing via tags will still work).

**Action when adding an agent**: See [TODO.md P2-14](TODO.md#p2-14-dynamic-python_class-import-in-manifests) for the long-term fix. Short-term: update all 4 locations listed above.

---

## 2. Token Budgets

| Attribute | Canonical Source | Derived / Duplicated In |
|-----------|-----------------|------------------------|
| Per-agent input/output limits | `forge/agents/<id>/agent.yaml` → `context_budget:` | None (authoritative) |
| `ConversationContext.max_history` | `src/orchestrator/context.py` — `add_user_message(max_history=200)` | Limits in-memory message list to 200 entries (P1-9) |
| Default budget (if agent YAML omits it) | `forge/_context_window.yaml` → `defaults:` | Hardcoded fallback in `src/forge/context_budget.py` line ~80 (`16000`/`8000`) |
| Token counting library | `tiktoken>=0.7.0` in `pyproject.toml` | Falls back to `len(text) // 4` if tiktoken is not installed (P0-1 resolved) |
| Global hard cap | `forge/_context_window.yaml` → `governance.context_window.hard_cap` | Checked at runtime by `GovernanceGuardian` |
| Warning threshold | `forge/_context_window.yaml` → `governance.context_window.warning_threshold` | Checked at runtime by `GovernanceGuardian` |
| Fan-out cap | `forge/_context_window.yaml` → `scaling.max_parallel_agents` | Used by `OrchestratorEngine._fan_out()` |

### Budget math constraint

```
plan_envelope + sub_plan_envelope + (fan_out_cap × max_specialist_envelope) ≤ hard_cap
```

Current values: `32K + 20K + 3×25K = 127K ≤ 128K` ✓

**When any budget changes**: Recalculate the sum and verify it stays ≤ hard_cap.

---

## 3. Routing & Intent Classification

| Attribute | Canonical Source | Derived / Duplicated In |
|-----------|-----------------|------------------------|
| Keyword patterns (regex) | `_BUILTIN_KEYWORD_ROUTES` in `src/orchestrator/router.py` | Tags in `agent.yaml` (additive, not duplicate) |
| Agent tags | `forge/agents/<id>/agent.yaml` → `tags:` | Registered dynamically by `ForgeLoader` |
| Default agent (fallback) | `_DEFAULT_AGENT` in `src/orchestrator/router.py` | None |
| LLM routing prompt | `get_llm_routing_prompt()` in `router.py` | Hardcoded agent descriptions that **duplicate** `agent.yaml` descriptions |
| WorkIQ hint boost | `route_with_context()` in `router.py` | None |

### Drift risk

`get_llm_routing_prompt()` contains hardcoded agent descriptions. When you
change an agent's description in `agent.yaml`, the LLM routing prompt
becomes stale.

**Fix**: TODO P3-16 (auto-generate from registry). Short-term: grep for the
agent name in `router.py` and update manually.

---

## 4. Prompts & Instructions

| Attribute | Canonical Source | Derived / Duplicated In |
|-----------|-----------------|------------------------|
| Agent system prompt | `forge/agents/<id>/prompts/system.md` | `_DEFAULT_PLAN_PROMPT` fallback in `src/agents/plan_agent.py` |
| Agent instructions | `forge/agents/<id>/instructions/*.md` | None |
| Shared prompts | `forge/shared/prompts/*.md` | None |
| Shared instructions | `forge/shared/instructions/*.md` | None |
| Plan decomposition prompt | `forge/plan/prompts/decomposition.md` | None |
| Plan routing prompt | `forge/plan/prompts/routing.md` | None |
| Plan strategy prompt | `forge/plan/prompts/strategy.md` | None |

### Drift risk

`PlanAgent` has a `_DEFAULT_PLAN_PROMPT` hardcoded as a fallback. If you edit
`forge/plan/prompts/system.md`, the fallback becomes stale. The fallback only
activates if the manifest fails to load.

---

## 5. Configuration & Settings

| Attribute | Canonical Source | Derived / Duplicated In |
|-----------|-----------------|------------------------|
| API keys, model names | `.env` file → loaded by `pydantic-settings` | `src/config.py` (`Settings` class) defines defaults |
| LLM model name | `Settings.openai_model` in `src/config.py` | Speculative defaults (`claude-opus-4.6`, etc.) — update when wiring LLM |
| Server port | `Settings.server_port` in `src/config.py` (default: `8080`) | None |
| Log level | `Settings.log_level` in `src/config.py` (default: `INFO`) | None |
| Forge directory path | `Settings.forge_dir` in `src/config.py` (default: `forge`) | None |

### Drift risk

Model names in `config.py` (`claude-opus-4.6`, `codex-5.3`, `gemini-pro-3.1`)
are the current defaults. When LLM integration lands (TODO P0-5), verify
these match real, available model identifiers from each provider.

---

## 6. Governance & Selectors

| Attribute | Canonical Source | Derived / Duplicated In |
|-----------|-----------------|------------------------|
| Governance rules | `src/governance/guardian.py` — `GovernanceGuardian` class | Thresholds loaded from `_context_window.yaml` at runtime |
| `count_tokens()` public API | `src/governance/guardian.py` — `GovernanceGuardian.count_tokens()` | Called by `engine.py` (replaces direct `_budget_manager` access — P0-3) |
| HITL selector pattern | No formal interface | Implementations: `PlanSelector`, `GovernanceSelector`, `WorkIQSelector` (see TODO P2-13) |
| Agent lifecycle HITL | `src/governance/selector.py` — `AgentLifecycleReview` dataclass + 6 lifecycle methods | Consumed by `OrchestratorEngine.disable_agent()`, `unregister_agent()`. Fail-CLOSED on timeout. |
| Agent lifecycle management | `src/orchestrator/engine.py` — `disable_agent()`, `enable_agent()`, `unregister_agent()`, `list_enabled_agents()`, `list_disabled_agents()` | Exposed via `server.py` HTTP endpoints. `enable_agent()` has no HITL gate. |
| Alert counter / IDs | `GovernanceGuardian._alert_counter` | Used for both alert IDs and suggestion IDs |
| Budget deallocation | `src/forge/context_budget.py` — `ContextBudgetManager.deallocate()` | Called by `engine.py` when an agent is disabled/removed |
| Routing deregistration | `src/orchestrator/router.py` — `IntentRouter.deregister_patterns()` | Called by `engine.py` when an agent is disabled/removed |

---

## 7. Tests & Fixtures

| Attribute | Canonical Source | Notes |
|-----------|-----------------|-------|
| Shared fixtures | `tests/conftest.py` | Creates agents WITHOUT manifests (fallback constructors) |
| Engine fixtures | `tests/conftest.py` | **Do NOT use** `budget_manager` or `governance_guardian` — tests bypass enforcement path |

### Drift risk

Test fixtures create agents with hardcoded constructor args, not from
`agent.yaml` manifests. If you change manifest structure, tests won't catch
breakage in the manifest-loading path.

**Fix**: Add fixtures that call `BaseAgent.from_manifest()` with real YAML files.

---

## 8. File Registry

Quick reference for where each category of files lives.

| Category | Path | Canonical? |
|----------|------|-----------|
| Agent manifests | `forge/agents/<id>/agent.yaml` | ✓ Canonical |
| Plan agent manifest | `forge/plan/agent.yaml` | ✓ Canonical |
| Context window config | `forge/_context_window.yaml` | ✓ Canonical |
| Informational registry | `forge/_registry.yaml` | ⚠ Informational only — **not** used by `ForgeLoader` (loader walks directories) |
| Python source | `src/` | ✓ Canonical |
| Tests | `tests/` | ✓ Canonical |
| Changelog | `CHANGELOG.md` | ✓ Canonical — record all changes here |
| Backlog | `TODO.md` | ✓ Canonical — track all planned work here |
| Architecture guide | `GUIDE.md` | ✓ Reference (may lag behind code) |
| Maintenance guide | `GUIDE2.md` | ✓ Reference (may lag behind code) |
| LLM architecture ref | `ARCHITECTURE.md` | ✓ Compact architecture (~250 lines, LLM-optimised) |
| LLM instructions | `.github/copilot-instructions.md` | ✓ LLM first-read file (~140 lines) |

---

## 9. Update Protocol

When you make a change to ProtoForge:

1. **Identify canonical source** in this file
2. **Update canonical source** first
3. **Update all derived copies** listed in the "Derived / Duplicated In" column
4. **Record the change** in [CHANGELOG.md](CHANGELOG.md) under `[Unreleased]`
5. **Update TODO.md** if this completes a backlog item (move to Completion Log)
6. **Run tests**: `.venv\Scripts\python.exe -m pytest`
7. **Commit** with a descriptive message referencing the TODO item (e.g., `P0-3: fix governance encapsulation leak`)

---

*Last updated: 2026-02-23 — ProtoForge v0.1.1*
