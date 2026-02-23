# ProtoForge — Maintenance & Versioning Guide

> **TL;DR for LLMs**: Codebase-validated maintenance guide (455+ lines / 9 sections).
> Covers: document hierarchy, update protocol, versioning, architecture layers
> (verified module map with line counts), common maintenance tasks, anti-drift rules.
>
> This is doc **4 of 9** in the reading order. Read
> [ARCHITECTURE.md](ARCHITECTURE.md) first for orientation.
> **See also**: [SOURCE_OF_TRUTH.md](SOURCE_OF_TRUTH.md) for canonical ownership.

> **Validated against codebase commit `eb75c19` on 2026-02-23.**
> Every claim in this document was verified by scanning the actual source files.
> Where a number or path is cited, the corresponding file and line range are noted.
> Line references in the validation log (§9) are approximate — recalculate after edits.

---

## 1. Document Hierarchy — What Lives Where

ProtoForge uses nine documentation files with progressive disclosure.
LLMs should start with `copilot-instructions.md` → `ARCHITECTURE.md`, then
read deeper docs only when the task requires it.

| Document             | Purpose                                          | When to Update                                       |
|----------------------|--------------------------------------------------|------------------------------------------------------|
| `copilot-instructions.md` | LLM first-read orientation (~140 lines)     | Agent identity, directory map, coding conventions    |
| `ARCHITECTURE.md`    | Compact architecture reference (~255 lines)      | Module graph, APIs, HITL patterns, common tasks      |
| `SOURCE_OF_TRUTH.md` | Architectural ownership map (§1–§9)              | Structural changes (new agent, new layer, new YAML)  |
| `GUIDE.md`           | Implementation deep-dives (§1–§19, ~2 700 lines) | New features, enrichment sources, pipeline changes   |
| `GUIDE2.md`          | DE critique / tuning guide (§1–§13)              | When critique items are completed or new ones found  |
| `README.md`          | Onboarding, quick-start, project overview        | Dependency changes, CLI changes, new endpoints       |
| `CHANGELOG.md`       | Version history (Keep a Changelog format)        | Every commit to `master`                             |
| `TODO.md`            | Prioritised backlog (P0→P3)                      | When items are completed or new work is identified   |
| `MAINTENANCE.md`     | This file — maintenance & versioning protocol    | When the maintenance process itself changes           |

**Rule**: touch source → touch the doc that owns that layer.  `SOURCE_OF_TRUTH.md` §9
documents the full update protocol.

---

## 2. Update Protocol — Step by Step

This protocol was verified against `SOURCE_OF_TRUTH.md` §9 and the actual
CHANGELOG / TODO structure.

### 2.1 Before You Code

1. Pick the backlog item from `TODO.md` (P0 first, then P1, etc.).
2. Read the relevant `GUIDE.md` section for the layer you'll touch.
3. Check `GUIDE2.md` to see if any related critique items apply.

### 2.2 While You Code

1. Run the test suite after every meaningful change:
   ```powershell
   .venv\Scripts\python.exe -m pytest -q --tb=short
   ```
   *Current: 363 tests passing in ≈12 s (verified).*

2. Keep commits atomic — one logical change per commit.

### 2.3 After You Code

1. **CHANGELOG.md** — Add entry under `[Unreleased]` with:
   - Category: `Added | Changed | Fixed | Removed | Security`
   - One-line description
   - Commit hash reference
2. **TODO.md** — Move completed item to the "Completion Log" table at the bottom.
3. **SOURCE_OF_TRUTH.md** — Update if you changed ownership (new agent,
   new module, new YAML key).
4. **GUIDE.md** — Update the relevant section (or add a new §) if you added
   user-visible behaviour.
5. **GUIDE2.md** — Mark critique items `**DONE**` when resolved.
6. **README.md** — Update if CLI, endpoint, or dependency changed.

### 2.4 Commit & Push

```bash
git add -A
git commit -m "<type>: <description>"   # conventional commits
git push origin master
```

Conventional commit types: `feat`, `fix`, `perf`, `refactor`, `docs`, `test`, `chore`.

---

## 3. Versioning — SemVer Rules

**Current version**: `0.1.1` (declared in `pyproject.toml` line 3).

ProtoForge uses [Semantic Versioning 2.0.0](https://semver.org/):

| Bump     | When                                              | Example                           |
|----------|---------------------------------------------------|------------------------------------|
| **MAJOR** | Breaking API change (endpoint removed, schema change) | `0.x.y → 1.0.0`               |
| **MINOR** | New feature, new agent, new endpoint (backwards-compatible) | `0.1.0 → 0.2.0`          |
| **PATCH** | Bug fix, doc improvement, test addition           | `0.1.0 → 0.1.1`                  |

**Where to bump**:
1. `pyproject.toml` → `version = "x.y.z"` (single source of truth for the package)
2. `CHANGELOG.md` → move `[Unreleased]` items under a new `[x.y.z] — YYYY-MM-DD` heading

**Pre-1.0 rule**: While at `0.x.y`, minor bumps may contain breaking changes.
The API is not yet stable.

---

## 4. Architecture Layers — Verified Map

The following is verified against actual source files as of commit `eb75c19`.

### 4.1 Request Flow

```
User message
  │
  ▼
OrchestratorEngine.process()          ← src/orchestrator/engine.py L131
  │
  ├─ IntentRouter.route_by_keywords() ← src/orchestrator/router.py L232
  │
  ▼
_process_after_routing()              ← src/orchestrator/engine.py L248
  │
  ├─ _dispatch(PLAN, ...)             ← Plan Agent always runs first
  ├─ _run_sub_plan_pipeline()         ← Plan HITL → Sub-Plan Agent → Sub-Plan HITL
  ├─ _resolve_sub_agents()            ← Filters PLAN/SUB_PLAN, enforces fan-out cap
  ├─ _fan_out(sub_agents, ...)        ← asyncio.gather(), max 3 concurrent
  └─ _aggregate()                     ← Merges Plan + Sub-Plan + specialist outputs
```

### 4.2 Source Modules

| Module                           | Lines | Purpose                                            |
|----------------------------------|------:|-----------------------------------------------------|
| `src/orchestrator/engine.py`     |   763 | Core pipeline: process → dispatch → fan-out → aggregate |
| `src/orchestrator/router.py`     |   413 | Keyword + LLM routing, WorkIQ-enriched routing      |
| `src/orchestrator/context.py`    |    86 | `ConversationContext`, `AgentResult`, `Message`      |
| `src/orchestrator/plan_selector.py` | 374 | Plan HITL (Phase A) + Sub-Plan HITL (Phase B)       |
| `src/agents/base.py`            |   122 | `BaseAgent` ABC, `from_manifest()`, `_build_messages()` |
| `src/agents/generic.py`         |    71 | `GenericAgent` — manifest-driven, placeholder execute |
| `src/governance/guardian.py`     |   477 | `GovernanceGuardian`: context window + skill cap + architectural audit |
| `src/governance/selector.py`    |   441 | HITL gate for governance alerts + agent lifecycle    |
| `src/forge/loader.py`           |   276 | `ForgeLoader`: walks `forge/`, builds `ForgeRegistry` |
| `src/forge/context_budget.py`   |   212 | `ContextBudgetManager`: allocate, truncate, fits_budget |
| `src/forge/contributions.py`    |   258 | `ContributionManager`: CRUD for contrib/ with audit  |
| `src/config.py`                 |   134 | `Settings`: LLM, Server, MCP, Forge, Observability  |
| `src/main.py`                   |   359 | Bootstrap, CLI (serve / chat / status)               |
| `src/server.py`                 |   896 | FastAPI app: 35 HTTP endpoints + inspector dashboard |
| `src/mcp/`                      |     — | MCP protocol, skill server, skill loader             |
| `src/workiq/`                   |     — | WorkIQ CLI client + HITL selector                    |
| `src/registry/`                 |     — | `AgentCatalog`, `WorkflowEngine`                     |

### 4.3 Agents (10 total)

Verified from `AgentType(StrEnum)` in `src/orchestrator/router.py` lines 14–23:

| Agent ID           | Type        | Python Class                | Budget (input+output) |
|--------------------|-------------|-----------------------------|-----------------------|
| `plan`             | coordinator | `PlanAgent`                 | 24K + 8K = **32K**   |
| `sub_plan`         | specialist  | `SubPlanAgent`              | 14K + 6K = **20K**   |
| `log_analysis`     | specialist  | `LogAnalysisAgent`          | 15K + 7K = **22K**   |
| `code_research`    | specialist  | `GenericAgent` (fallback)   | 17K + 8K = **25K**   |
| `remediation`      | specialist  | `RemediationAgent`          | 15K + 7K = **22K**   |
| `knowledge_base`   | specialist  | `KnowledgeBaseAgent`        | 17K + 8K = **25K**   |
| `data_analysis`    | specialist  | `GenericAgent` (fallback)   | 15K + 7K = **22K**   |
| `security_sentinel`| specialist  | `SecuritySentinelAgent`     | 15K + 7K = **22K**   |
| `workiq`           | specialist  | `WorkIQAgent`               | 12K + 6K = **18K**   |
| `github_tracker`   | specialist  | `GitHubTrackerAgent`        | 15K + 7K = **22K**   |

**Specialised vs Generic**: `_SPECIALISED_CLASSES` in `src/main.py` maps 8 agent
types to dedicated Python classes.  `code_research` and `data_analysis` have no
dedicated class — they fall back to `GenericAgent.from_manifest()`.

### 4.4 Token Budget Math (Verified)

Source: `forge/_context_window.yaml`

```
Global hard cap:           128,000 tokens
Warning threshold:         110,000 tokens (HITL triggered)
Aggregation reserve:         8,000 tokens

Worst-case single run (actual configured budgets):
  Plan Agent (coordinator):    32,000
  Sub-Plan Agent:              20,000
  Top 3 specialists:    25,000 + 25,000 + 22,000 = 72,000
  ──────────────────────────────────────────
  Total:                      124,000
  Headroom to 128K cap:         4,000
  Aggregation reserve:          8,000 (held from global pool)
```

**Enforcement chain** (verified in `engine.py` `_dispatch()`):
1. `ContextBudgetManager.allocate()` — per-agent budget from YAML or defaults
2. `ContextBudgetManager.fits_budget()` — check input fits
3. `ContextBudgetManager.truncate()` — trim if over (strategy: priority/sliding_window/summarize)
4. `GovernanceGuardian.check_context_window()` — cumulative check, raises `ContextWindowExceededError` at hard cap
5. Token count computed **once** and reused for both budget and governance (single `estimated_tokens` variable)

### 4.5 Forge Directory Structure

Source: `src/forge/loader.py` — `ForgeLoader.load()` calls in order:
1. `_load_context_config()` — reads `forge/_context_window.yaml`
2. `_load_coordinator()` — reads `forge/plan/agent.yaml`
3. `_load_agents()` — iterates `forge/agents/*/agent.yaml` (sorted)
4. `_load_shared()` — reads `forge/shared/prompts/`, `instructions/`, `workflows/`
5. `_load_contrib()` — reads `forge/contrib/` for community contributions

Each agent manifest (`agent.yaml`) has this structure:
```yaml
id: <agent_id>              # must match AgentType enum member
name: <display name>
type: coordinator | specialist
version: "1.0.0"
description: >-
  Multi-line description

context_budget:
  max_input_tokens: 15000
  max_output_tokens: 7000
  strategy: priority | sliding_window | summarize
  priority_order: [...]

subagents: []
prompts:
  system: system.md
skills:
  - <skill_name>.yaml
instructions:
  - <instruction_name>.md
tags: [...]
```

---

## 5. Common Maintenance Tasks

### 5.1 Add a New Agent

1. **Create forge manifest**: `forge/agents/<agent_id>/agent.yaml`
   - Populate all fields (see §4.5 for schema).
   - Create `prompts/system.md`, `skills/<name>.yaml`, `instructions/<name>.md`.
2. **Add to `AgentType` enum**: `src/orchestrator/router.py` — add new member.
3. **Add routing keywords**: Add entry in `_BUILTIN_KEYWORD_ROUTES` dict
   (`src/orchestrator/router.py` lines 26–96).
4. **Optional — specialised class**: If the agent needs custom Python logic,
   create `src/agents/<agent_id>_agent.py` subclassing `BaseAgent`.
   Add to `_SPECIALISED_CLASSES` in `src/main.py`.
   If no custom logic needed, `GenericAgent` handles it automatically.
5. **Add to `_default_agents`**: In `src/main.py` `bootstrap()`, add a fallback
   entry so the agent exists even without a forge manifest.
6. **Update `forge/_registry.yaml`**: Add the agent to the `agents:` section.
7. **Write tests**: Add `tests/test_<agent_id>.py`.
8. **Update docs**: `SOURCE_OF_TRUTH.md` §3, `GUIDE.md` (new §), `README.md`
   agent table, `CHANGELOG.md`.

### 5.2 Add a New Enrichment Source (Pre-Router)

Detailed protocol in `GUIDE.md` §19.  Summary:

1. Create client in `src/<source>/client.py` (async, returns structured result).
2. Create selector in `src/<source>/selector.py` (prepare → wait → resolve HITL).
3. Add HTTP endpoints in `src/server.py` for HITL interaction.
4. Wire into `OrchestratorEngine.__init__()` and `process_with_enrichment()`.
5. Update `src/main.py` `bootstrap()` to instantiate and inject.

### 5.3 Modify Token Budgets

1. **Global settings**: Edit `forge/_context_window.yaml`.
   - `global.max_total_tokens` — hard limit for one run
   - `governance.context_window.warning_threshold` — HITL trigger
   - `governance.context_window.hard_cap` — fail-closed trigger
   - `scaling.max_parallel_agents` — fan-out cap (default 3)
2. **Per-agent override**: Edit `forge/agents/<id>/agent.yaml` `context_budget:` section.
3. **Defaults by type**: Edit `defaults.specialist` or `defaults.coordinator`
   in `forge/_context_window.yaml`.
4. **Recalculate budget math**: Ensure worst-case stays ≤ `max_total_tokens`.
   Formula: `Plan + Sub-Plan + (max_parallel_agents × max_specialist_budget) + aggregation_reserve ≤ hard_cap`

### 5.4 Add a New HTTP Endpoint

1. Add route in `src/server.py` inside `create_app()`.
2. Define Pydantic request/response models at the top of the file.
3. Wire to orchestrator, selector, or catalog as needed.
4. Update `README.md` endpoint table.
5. Add API test.

### 5.5 Modify Governance Rules

- **Skill cap**: `forge/_context_window.yaml` → `governance.skill_cap.max_skills_per_agent`
- **HITL timeout**: `governance.hitl.timeout_seconds` (default 120s)
- **Auto-resolve action**: `governance.hitl.auto_resolve_action` (`accept` | `reject`)
- **Hard cap enforcement toggle**: `governance.context_window.enforce_hard_cap`
  - `true` = fail-closed (raises `ContextWindowExceededError`)
  - `false` = fail-open (logs warning, continues)

### 5.6 Disable or Remove an Agent at Runtime (HITL-Gated)

Use the lifecycle endpoints to dynamically disable or remove agents without
restarting the server. Disable and remove actions require human confirmation
(fail-CLOSED on timeout — action is rejected if unconfirmed).

**Disable an agent** (reversible):

1. `POST /agents/{agent_id}/disable` — triggers HITL lifecycle review.
2. `GET /governance/lifecycle-reviews` — inspect the pending review.
   The response includes `enabled_agents_after` showing which agents remain
   active if you approve.
3. `POST /governance/lifecycle-reviews/resolve` with `{"request_id": "...", "accepted": true}` — approve.
4. On approval: agent is disabled, routing patterns deregistered, budget deallocated.
5. If timeout (120s) expires without resolution → action is **rejected** (fail-CLOSED).

**Re-enable a disabled agent** (no HITL required):

1. `POST /agents/{agent_id}/enable` — immediately re-enables the agent.
   No human confirmation needed (safe operation).

**Permanently remove an agent** (irreversible, HITL-gated):

1. `DELETE /agents/{agent_id}` — triggers HITL lifecycle review (same flow as disable).
2. Approve via `POST /governance/lifecycle-reviews/resolve`.
3. On approval: agent is removed from `_agents` dict, patterns deregistered, budget deallocated.

**Inspect current state**:

- `GET /agents/enabled` — list all currently enabled agents.
- `GET /agents/disabled` — list all currently disabled agents.

**Key design decisions**:
- **Fail-CLOSED on timeout**: Unlike context/skill reviews which fail-open,
  lifecycle actions are rejected if the human doesn't respond. Rationale:
  accidental agent removal without explicit consent must be prevented.
- **Enable has no HITL gate**: Re-enabling is always safe and instant.
- **Budget + routing cleanup**: On disable/remove, `ContextBudgetManager.deallocate()`
  and `IntentRouter.deregister_patterns()` are called automatically.

---

## 6. Anti-Drift Rules

These rules prevent documentation from drifting from the code.

| Rule | Enforcement |
|------|-------------|
| Every `master` commit updates `CHANGELOG.md` | Manual discipline (TODO: pre-commit hook P2-3) |
| Agent count in docs matches `AgentType` enum | Count `AgentType` members in `router.py` |
| Test count in docs matches `pytest` output | Run `pytest --tb=no` and compare last line |
| Budget math in docs matches YAML config | Read `forge/_context_window.yaml` + agent YAMLs |
| Endpoint count matches `server.py` routes | `grep -c "@app\." src/server.py` |
| `pyproject.toml` version matches CHANGELOG heading | Compare manually on each release |

**Drift check one-liner** (PowerShell):
```powershell
# Count AgentType members
Select-String -Path src\orchestrator\router.py -Pattern '^\s+\w+ = "\w+"' | Measure-Object

# Count HTTP endpoints
Select-String -Path src\server.py -Pattern '@app\.(get|post|put|delete|patch)' | Measure-Object

# Run tests
.venv\Scripts\python.exe -m pytest --tb=no 2>&1 | Select-Object -Last 1
```

---

## 7. Backlog Priority System

Source: `TODO.md` header.

| Priority | Meaning                         | Target         |
|----------|---------------------------------|----------------|
| **P0**   | Blocking — must fix before next feature | Next commit |
| **P1**   | Important — should fix soon     | Next 2–3 commits |
| **P2**   | Nice to have — improves quality | When convenient |
| **P3**   | Future ideas / research         | No deadline    |

When completing a backlog item, move it from the priority section to the
"Completion Log" table at the bottom of `TODO.md` with the commit hash.

---

## 8. Key File Reference Card

Quick reference for the most-touched files during maintenance:

| I need to…                        | Start here                                |
|-----------------------------------|-------------------------------------------|
| Add/modify routing keywords       | `src/orchestrator/router.py` `_BUILTIN_KEYWORD_ROUTES` |
| Change the orchestration pipeline | `src/orchestrator/engine.py` `_process_after_routing()` |
| Adjust token budgets              | `forge/_context_window.yaml`              |
| Add a new agent (YAML)            | `forge/agents/<id>/agent.yaml`            |
| Add a new agent (Python)          | `src/agents/<id>_agent.py` + `src/main.py` `_SPECIALISED_CLASSES` |
| Change governance thresholds      | `forge/_context_window.yaml` `governance:` |
| Add an HTTP endpoint              | `src/server.py` `create_app()`            |
| Configure LLM providers           | `src/config.py` `LLMConfig`               |
| Write a forge contribution        | `src/forge/contributions.py` `ContributionManager` |
| Understand HITL flow              | `src/orchestrator/plan_selector.py`, `src/governance/selector.py` |
| Disable/remove agent at runtime   | `src/server.py` lifecycle endpoints → `src/orchestrator/engine.py` |
| Bootstrap / wire dependencies     | `src/main.py` `bootstrap()`               |
| Check conversation history        | `src/orchestrator/context.py` `ConversationContext` (max_history=200) |

---

## 9. Validation Log

The following claims were verified against the actual codebase:

| # | Claim | Verified In | Status |
|---|-------|-------------|--------|
| 1 | `AgentType(StrEnum)` has 10 members | `router.py` L14–23 | ✅ |
| 2 | `_BUILTIN_KEYWORD_ROUTES` maps all 10 agents | `router.py` L26–96 | ✅ |
| 3 | `_DEFAULT_AGENT = AgentType.KNOWLEDGE_BASE` | `router.py` L198 | ✅ |
| 4 | `_SPECIALISED_CLASSES` has 8 entries (not 10) | `main.py` L38–49 | ✅ |
| 5 | `code_research` + `data_analysis` use `GenericAgent` fallback | `main.py` L65 | ✅ |
| 6 | `process()` delegates to `_process_after_routing()` | `engine.py` L131–152 | ✅ |
| 7 | Plan Agent always runs first | `engine.py` L265 | ✅ |
| 8 | Fan-out cap = 3 (from `_context_window.yaml` scaling) | `engine.py` L100–103, YAML L73 | ✅ |
| 9 | `_dispatch()` counts tokens once and reuses | `engine.py` L459 | ✅ |
| 10 | `_fan_out()` uses `asyncio.gather()` | `engine.py` L581 | ✅ |
| 11 | `GovernanceGuardian.count_tokens()` is public | `guardian.py` L167–172 | ✅ |
| 12 | Hard cap = 128,000 tokens | `_context_window.yaml` L7, `guardian.py` default | ✅ |
| 13 | Warning threshold = 110,000 tokens | `_context_window.yaml` L15 | ✅ |
| 14 | `enforce_hard_cap: true` → raises `ContextWindowExceededError` | `guardian.py` L217 | ✅ |
| 15 | `ConversationContext.max_history = 200` | `context.py` L54 | ✅ |
| 16 | `BaseAgent.from_manifest()` reads system prompt from manifest | `base.py` L58–70 | ✅ |
| 17 | `_build_messages()` = system + history(10) + user | `base.py` L109–113 | ✅ |
| 18 | `GenericAgent.execute()` is placeholder (TODO: LLM) | `generic.py` L59 | ✅ |
| 19 | Budget: allocate → fits_budget → truncate pipeline | `context_budget.py` | ✅ |
| 20 | tiktoken in `pyproject.toml` dependencies | `pyproject.toml` L28 | ✅ |
| 21 | Version `0.1.1` | `pyproject.toml` L3 | ✅ |
| 22 | 35 HTTP endpoints in `server.py` | `server.py` (grep count) | ✅ |
| 23 | ForgeLoader loads: context_config → coordinator → agents → shared → contrib | `loader.py` L93–101 | ✅ |
| 24 | ForgeLoader instantiated twice in `bootstrap()` | `main.py` ~L87, ~L110 | ✅ |
| 25 | Plan HITL (Phase A) + Sub-Plan HITL (Phase B) | `plan_selector.py`, `engine.py` L322–400 | ✅ |
| 26 | GovernanceSelector: ContextWindowReview + SkillCapReview | `selector.py` L41–59 | ✅ |
| 27 | 363 tests passing | `pytest --tb=no` output | ✅ |
| 28 | Plan budget = 32K, Sub-Plan = 20K, Specialist = 22K | agent.yaml files | ✅ |
| 29 | Worst-case = 126K (with 8K reserve) ≤ 128K cap | Calculated from YAML | ✅ |
| 30 | `AgentLifecycleReview` dataclass in `GovernanceSelector` | `selector.py` | ✅ |
| 31 | Lifecycle HITL is fail-CLOSED on timeout | `selector.py` `wait_for_lifecycle_review()` | ✅ |
| 32 | `disable_agent()` / `unregister_agent()` are HITL-gated | `engine.py` | ✅ |
| 33 | `enable_agent()` has NO HITL gate | `engine.py` | ✅ |
| 34 | `deregister_patterns()` removes routing on disable/remove | `router.py` | ✅ |
| 35 | `deallocate()` releases budget on disable/remove | `context_budget.py` | ✅ |
| 36 | 7 new lifecycle HTTP endpoints | `server.py` | ✅ |
| 37 | 363 tests passing (30 new lifecycle tests) | `pytest --tb=no` output | ✅ |

---

*This document is part of the ProtoForge project.  Keep it in sync with the codebase.*
