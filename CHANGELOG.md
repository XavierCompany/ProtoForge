# Changelog

All notable changes to ProtoForge are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Version numbering follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added
- `CHANGELOG.md` — project changelog (this file)
- `TODO.md` — prioritised backlog derived from GUIDE2 §13
- `SOURCE_OF_TRUTH.md` — canonical ownership map for agent identities, budgets, routing, and prompts

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
- **328 tests passing** across 10 test files (routing, orchestration, governance, budget, forge loading, sub-plan, workiq, github tracker)

### Known Limitations (0.1.0)
- Every `execute()` returns placeholder strings — no LLM calls wired
- `_route_with_llm()` returns `None` (stub)
- `_truncate_summarize()` falls back to `priority` strategy
- Token counting uses `len(text) // 4` fallback (tiktoken not in dependencies)
- `protoforge serve` exits with code 1 (missing env vars / credentials)
- System Python 3.14 cannot build pydantic-core (requires Rust); use venv with Python 3.12

---

## Commit History Reference

| Commit | Description |
|--------|-------------|
| `3c1c827` | Context window optimization — full budget enforcement pipeline |
| `32e0e35` | Documentation update |
| `8fcd3dd` | Governance Guardian system — 316 tests |
| `82986b5` | GitHub Tracker Agent — 248 tests |

---

[Unreleased]: https://github.com/XavierCompany/ProtoForge/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/XavierCompany/ProtoForge/releases/tag/v0.1.0
