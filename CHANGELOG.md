# Changelog

> **TL;DR for LLMs**: Version history (180+ lines). Current version: **0.1.1**.
> Two releases: v0.1.1 (lifecycle HITL + P0 fixes) and v0.1.0 (initial release).
>
> This is doc **6 of 10** in the reading order.
> See [ARCHITECTURE.md](ARCHITECTURE.md) for the system overview.

All notable changes to ProtoForge are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Version numbering follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added
- **LLM Intelligence (P0-5)** — all agents now call `_call_llm()` via `BaseAgent`, delegating to singleton `LLMClient` in `src/llm/client.py`. Supports Azure AI Foundry (`DefaultAzureCredential`), API key auth, and direct OpenAI. Graceful degradation — returns `None` when unconfigured.
- **`src/llm/` package** — `LLMClient` (async, singleton, multi-provider), `get_llm_client()` factory
- **`_call_llm()` in `BaseAgent`** — all 8 specialist agents + PlanAgent + SubPlanAgent enriched with domain context before LLM call
- **`OrchestratorEngine._route_with_llm()`** — JSON intent-classification prompt → `RoutingDecision` dataclass
- **30 mocked LLM tests** in `tests/test_llm.py` — covers all providers, degradation paths, agent LLM paths, engine routing
- **13 live integration tests** in `tests/test_llm_live.py` — end-to-end tests hitting real Azure OpenAI (`gpt-4o-mini` via `DefaultAzureCredential`). Gated by `@pytest.mark.live` marker. Run: `pytest -m live`
- **`live` pytest marker** in `pyproject.toml` — deselect with `-m "not live"`
- **Azure OpenAI resource** — `protoforge-openai` in `protoforge-rg` (eastus2), model `gpt-4o-mini` (2024-07-18), `Cognitive Services OpenAI User` RBAC assigned
- **`.env.example`** updated with realistic Azure endpoint/model/API version
- **BUILDING_AGENTS.md** — practical tutorial for building a new agent with Azure AI Foundry, covering Plan Agent conversation, LLM wiring, and full pipeline walkthrough (~350 lines)
- **Document Map** in README.md — human-friendly navigation table with recommended reading orders for humans vs LLMs

### Changed (documentation validation phase 3)
- **`config.py`**: `azure_model` default `gpt-5.3-codex` → `gpt-4o-mini`, `azure_api_version` default `2026-01-01` → `2024-10-21` (matches deployed resource)
- **GUIDE2.md §1**: LLM inference and LLM-based routing changed from "Stub" to "Working"
- **GUIDE2.md §1**: Test count updated from 378 to 421 (12 test files)
- **TODO.md**: TL;DR updated — "P0: 5 items (4 done, 1 remaining)" → "P0: 5 items (**5 done**)"
- **TODO.md**: P3-19 (Integration tests with real LLM) marked `[x]` with live test details
- **README.md**: Model table updated — Azure shows `gpt-4o-mini` (deployed), added `az login` to Quick Start
- **README.md**: Test counts updated to 421 across 12 files, added `test_llm.py` (30) and `test_llm_live.py` (13)
- **README.md**: "Next Engineer" table — LLM wiring changed from "stub today" to working reference
- **ARCHITECTURE.md**: Added `src/llm/` to module dependency graph, updated test count 378 → 421 (12 files)
- **ARCHITECTURE.md**: Added `LLMClient`/`get_llm_client` to Public APIs, expanded `LLMConfig` description
- **BUILDING_AGENTS.md**: Rewritten from ~635 lines to ~195 lines — minimal, focused 8-step tutorial
- **GUIDE2.md**: Fixed "9 of 9" → "9 of 10" in reading order header
- **GUIDE2.md**: Fixed §2.5 bootstrap() size "120-line" → "~180-line" (actual: lines 80–258)
- **GUIDE2.md**: Fixed §2.8 code snippet to show `max_history` as class field (not method parameter)
- **GUIDE2.md**: Fixed §2.10 server.py size "~900 lines" → "~750 lines" (actual: 752)
- **GUIDE2.md**: Fixed §1 table — `_route_with_llm()` → `get_llm_routing_prompt()` (function was renamed)
- **TODO.md**: Fixed "5 of 9" → "5 of 10" in reading order header
- **TODO.md**: Fixed P1-6 bootstrap() size "120+" → "~180" lines
- **TODO.md**: Fixed P1-7 server.py size "~900-line" → "~750-line"
- **copilot-instructions.md**: Updated BUILDING_AGENTS.md line count ~350 → ~195
- **LLM instruction variants**: Updated BUILDING_AGENTS.md line count in claude.md, cursorrules.md
- **Multi-model policy** — Claude Opus 4.6 (default), Codex 5.3, Gemini Pro 3.1 as first-class alternatives (see ADR-002)
- 4 new model-policy tests in `TestModelPolicy` class — validates config.py defaults for all providers (378 total, up from 374)
- `ALLOWED_MODELS` constant in `test_copilot_customization.py` — centralises model policy validation

### Changed
- `config.py`: Google model default `gemini-3-pro` → `gemini-pro-3.1` for consistent naming
- `README.md`: Model compatibility table reordered with updated defaults and first-class alternatives
- `GUIDE.md`: ADR-002 updated — Codex 5.3 and Gemini Pro 3.1 documented as first-class alternatives
- `SOURCE_OF_TRUTH.md`: §5 drift risk section updated with current model names
- `.env.example`: Google model names updated to `gemini-pro-3.1` / `gemini-pro-3.0`
- Agent/prompt frontmatter: `# Also allowed: Codex 5.3, Gemini Pro 3.1` comment added
- Model validation tests rewritten to check against `ALLOWED_MODELS` set (not exact string match)

### Fixed
- **`test_copilot_customization.py`**: `test_config_default_provider_is_anthropic` now isolated from `.env` via `monkeypatch` + `LLMConfig(_env_file=None)` — was failing when `.env` sets `DEFAULT_LLM_PROVIDER=azure_ai_foundry`
- **Documentation accuracy audit** — comprehensive pass to fix stale numbers, phantom files, and contradictions across all 9 docs
- **README.md**: Removed phantom `code_research_agent.py` and `data_analysis_agent.py` from project structure (these agents use `GenericAgent`, no dedicated files exist)
- **README.md**: Fixed endpoint count 26 → 35, test counts per file (governance 68 → 113, workiq 37 → 52, orchestrator 19 → 14, mcp 14 → 7, registry 10 → 9, sub_plan 29 → 30)
- **README.md**: Clarified agent file listing — "10 agent types (8 dedicated files + GenericAgent handles 2)"
- **README.md**: Fixed budget math formula to use actual configured budgets (25K + 25K + 22K = 124K)
- **ARCHITECTURE.md**: Fixed "9 implementations" → "10 implementations", "*_agent.py (7 specialists)" → "8 specialist files"
- **ARCHITECTURE.md**: Fixed budget formula from "= 127K" to "≤ 128K cap"
- **ARCHITECTURE.md**: Updated §10 reading order table with correct line counts (~5850 total)
- **MAINTENANCE.md**: Updated ALL module line counts in §4.2 (e.g., engine.py 644 → 763, guardian.py 386 → 477)
- **MAINTENANCE.md**: Fixed agent budget values in §4.3 — code_research and knowledge_base are 25K (not 22K), workiq is 18K (not 22K)
- **MAINTENANCE.md**: Fixed worst-case budget math in §4.4 — 25K + 25K + 22K = 124K (not 3×22K = 118K)
- **MAINTENANCE.md**: Updated validation commit reference from `72d25e8` to `eb75c19`
- **GUIDE2.md**: Fixed TL;DR "940+ lines" → "900+ lines", §2.10 "898 lines" → "~900 lines", budget math "127K" → "124K"
- **SOURCE_OF_TRUTH.md**: Fixed copilot-instructions.md "~120 lines" → "~140 lines"
- **copilot-instructions.md**: Fixed reading order positions 6-9 to match ARCHITECTURE.md and TL;DR headers, updated all per-doc line counts, total "~4550 lines (~18K tokens)" → "~5850 lines (~60K tokens)"
- **TODO.md**: Fixed P1-7 "898-line" → "~900-line"

### Fixed (continued — documentation audit phase 2)
- **README.md**: Fixed governance endpoint paths — removed non-existent `/governance/alerts/unresolved`, corrected `/governance/resolve-alert`, `/governance/context-reviews/resolve`, `/governance/skill-reviews/resolve`
- **README.md**: Added 3 missing GitHub endpoints (`/github/document-commit`, `/github/manage-issue`, `/github/changelog`)
- **README.md**: Fixed `forge/agents/` directory count — "8 specialist agents" → "9 agent directories (sub_plan + 8 specialists)"
- **GUIDE.md**: Fixed same governance endpoint path errors as README (§7 REST endpoints table)
- **GUIDE.md**: Fixed warning threshold from 120K → 110K in governance status example JSON (§7)
- **GUIDE.md**: Fixed "cumulative tokens > 120K" → "> 110K" in sub-agent spawning section (§6)
- **forge/_registry.yaml**: Added missing `sub_plan` agent entry
- **LLM instruction variants**: Updated reading order in claude.md, cursorrules.md, windsurfrules.md to include BUILDING_AGENTS.md (doc 10 of 10)

---

## [0.1.1] — 2026-02-23

### Added
- **Agent Lifecycle HITL** — disable/remove agents at runtime with human-in-the-loop confirmation (fail-CLOSED on timeout)
- `GovernanceSelector.AgentLifecycleReview` dataclass + 6 lifecycle methods (`prepare_lifecycle_review`, `resolve_lifecycle_review`, `wait_for_lifecycle_review`, `pending_lifecycle_reviews`, `get_lifecycle_review`, `cleanup_lifecycle_review`)
- `OrchestratorEngine.disable_agent()`, `enable_agent()`, `unregister_agent()`, `list_enabled_agents()`, `list_disabled_agents()` — full agent lifecycle management
- `IntentRouter.deregister_patterns()` — removes routing patterns at runtime when an agent is disabled/removed
- `ContextBudgetManager.deallocate()` — releases budget allocation when an agent is disabled/removed
- 7 new HTTP endpoints: `POST /agents/{id}/disable`, `POST /agents/{id}/enable`, `DELETE /agents/{id}`, `GET /governance/lifecycle-reviews`, `POST /governance/lifecycle-reviews/resolve`, `GET /agents/enabled`, `GET /agents/disabled`
- 30 new tests for lifecycle HITL (363 total, up from 333): `TestAgentLifecycleReviewSelector`, `TestEngineAgentLifecycle`, `TestLifecycleServerEndpoints`
- `MAINTENANCE.md` — codebase-validated maintenance and versioning guide with 29-point validation log
- `CHANGELOG.md` — project changelog (this file)
- `TODO.md` — prioritised backlog derived from GUIDE2 §13
- `SOURCE_OF_TRUTH.md` — canonical ownership map for agent identities, budgets, routing, and prompts
- `tiktoken>=0.7.0` dependency for accurate token counting (P0-1) — commit `4d5128c`
- `GovernanceGuardian.count_tokens()` public method — eliminates encapsulation leak (P0-3) — commit `4d5128c`
- `ConversationContext.max_history` parameter (default 200) — trims unbounded message list (P1-9) — commit `4d5128c`
- 5 new tests: history limit trimming (user + agent messages), `GovernanceGuardian.count_tokens()` (with/without budget manager, empty string)
- **GUIDE.md §19**: "How to Add a Pre-Router Enrichment Source" — step-by-step guide for wiring new data sources (call transcripts, Jira, Slack, etc.) into the pre-router enrichment pipeline
- **GUIDE.md §4**: Updated context window docs to reflect single-count-per-dispatch optimisation
- **GUIDE2.md**: Marked completed critique items (§2.2, §2.3, §2.4, §2.8, §2.15) as ✅ DONE
- **README.md**: Added "Next Engineer Quick Start" section — where to refine, how to change agents, how to add enrichment inputs

### Changed
- `_dispatch()` skips disabled agents (returns `AgentResult(confidence=0.0, content="Agent is disabled")`)
- `get_status()` now includes `enabled_agents` and `disabled_agents` lists
- `GovernanceSelector` docstring updated to reflect 3 review types (context window, skill cap, agent lifecycle)
- `server.py` docstring updated with 7 new lifecycle endpoint descriptions (35 total endpoints)
- All documentation refreshed to reflect 378 tests passing and lifecycle feature
- `process()` now delegates to `_process_after_routing()` — eliminates ~30 lines of duplicate pipeline code (P0-2) — commit `4d5128c`
- `_dispatch()` counts input tokens once and reuses the count for budget check, governance check, and post-dispatch recording (P0-4) — commit `4d5128c`
- `_dispatch()`: `ContextWindowExceededError` import moved to module top-level (P1-10) — commit `4d5128c`
- `_dispatch()`: governance token counting uses `self._governance.count_tokens()` instead of reaching through `self._governance._budget_manager` (P0-3) — commit `4d5128c`
- All documentation refreshed to reflect 378 tests passing (was 333)

---

## [0.1.0] — 2026-02-23

### Added — Core Orchestrator (commits up to `3c1c827`)
- **Plan-first architecture** — User → Router → Plan Agent (HITL) → Sub-Plan Agent (HITL) → Task Agents (max 3) → Aggregate
- **10 agents**: plan, sub_plan, log_analysis, code_research, remediation, knowledge_base, data_analysis, security_sentinel, workiq, github_tracker
- **Forge directory** (`forge/`) — YAML-driven manifests (`agent.yaml`), prompts, instructions, skills, and shared resources
- **ForgeLoader** — discovers agents by directory walk, resolves prompts/instructions from markdown files
- **IntentRouter** — keyword-based scoring with compiled regex patterns, WorkIQ hint boosting, LLM fallback stub
- **OrchestratorEngine** — `process()`, `_dispatch()`, `_fan_out()`, `_run_sub_plan_pipeline()`, `_aggregate()`
- **GenericAgent** — manifest-driven agent instantiated via `BaseAgent.from_manifest()` (execute is placeholder)
- **Specialised agents** — `PlanAgent`, `SubPlanAgent`, `LogAnalysisAgent`, `CodeResearchAgent`, `RemediationAgent`, `KnowledgeBaseAgent`, `DataAnalysisAgent`, `SecuritySentinelAgent`, `WorkIQAgent`, `GitHubTrackerAgent`
- **Budget enforcement pipeline** — `ContextBudgetManager.allocate()`, `truncate()`, `fits_budget()` wired into every `_dispatch()` call
- **Governance Guardian** — always-on enforcement with skill cap, context window monitoring, decomposition suggestions, and alert lifecycle
- **GovernanceSelector** — HITL gate for governance alerts (prepare → wait → resolve)
- **PlanSelector** — HITL gate for plan/sub-plan review
- **WorkIQSelector** — HITL gate for WorkIQ enrichment (2-phase pipeline)
- **WorkIQ client** — async subprocess wrapper for `workiq` CLI
- **Context window config** (`forge/_context_window.yaml` v1.2.0) — warning at 110K, hard cap 128K, fan-out cap 3
- **`ContextWindowExceededError`** — fail-closed hard cap abort
- **FastAPI server** (`src/server.py`) — 30+ endpoints including `/chat`, `/governance/*`, `/workiq/*`, `/github/*`, `/inspector/*`
- **MCP protocol layer** (`src/mcp/`) — skill registry mapping YAML skills to MCP tools
- **Registry** (`src/registry/`) — agent catalog and workflow discovery
- **Configuration** (`src/config.py`) — pydantic-settings with environment variable support
- **CLI** (`protoforge serve`) — Typer-based entry point
- **Documentation** — `GUIDE.md` (architecture reference), `GUIDE2.md` (maintenance & tuning guide)
- **333 tests passing** across 10 test files (routing, orchestration, governance, budget, forge loading, sub-plan, workiq, github tracker)

### Known Limitations (0.1.0)
- Every `execute()` returns placeholder strings — no LLM calls wired
- `_route_with_llm()` returns `None` (stub)
- `_truncate_summarize()` falls back to `priority` strategy
- `protoforge serve` exits with code 1 (missing env vars / credentials)
- System Python 3.14 cannot build pydantic-core (requires Rust); use venv with Python 3.12

---

## Commit History Reference

| Commit | Description |
|--------|-------------|
| `4d5128c` | Context window 128K optimisations (P0-1..4, P1-9, P1-10) — 333 tests |
| `3c1c827` | Context window optimization — full budget enforcement pipeline |
| `32e0e35` | Documentation update |
| `8fcd3dd` | Governance Guardian system — 316 tests |
| `82986b5` | GitHub Tracker Agent — 248 tests |

---

[0.1.1]: https://github.com/XavierCompany/ProtoForge/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/XavierCompany/ProtoForge/releases/tag/v0.1.0
