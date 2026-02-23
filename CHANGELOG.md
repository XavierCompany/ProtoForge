# Changelog

> **TL;DR for LLMs**: Version history (100+ lines). Current version: **0.1.1**.
> Two releases: v0.1.1 (lifecycle HITL + P0 fixes) and v0.1.0 (initial release).
>
> This is doc **6 of 9** in the reading order.
> See [ARCHITECTURE.md](ARCHITECTURE.md) for the system overview.

All notable changes to ProtoForge are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Version numbering follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
- All documentation refreshed to reflect 363 tests passing and lifecycle feature
- `process()` now delegates to `_process_after_routing()` — eliminates ~30 lines of duplicate pipeline code (P0-2) — commit `4d5128c`
- `_dispatch()` counts input tokens once and reuses the count for budget check, governance check, and post-dispatch recording (P0-4) — commit `4d5128c`
- `_dispatch()`: `ContextWindowExceededError` import moved to module top-level (P1-10) — commit `4d5128c`
- `_dispatch()`: governance token counting uses `self._governance.count_tokens()` instead of reaching through `self._governance._budget_manager` (P0-3) — commit `4d5128c`
- All documentation refreshed to reflect 363 tests passing (was 333)

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
