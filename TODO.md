# TODO — ProtoForge Backlog

> **TL;DR for LLMs**: Prioritised backlog (240+ lines).
> P0: 5 items (**5 done**).
> P1: 5 items (3 done, 2 remaining). P2: 5 items (1 done, 4 remaining). P3: 5 items (1 done).
>
> This is doc **5 of 10** in the reading order.
> See [ARCHITECTURE.md](ARCHITECTURE.md) for system overview.

> **Source**: Derived from [GUIDE2.md §13 — Improvement Roadmap](GUIDE2.md#13-improvement-roadmap)
> **Tracking**: Update status here. Record completions in [CHANGELOG.md](CHANGELOG.md).
> **Canonical ownership**: See [SOURCE_OF_TRUTH.md](SOURCE_OF_TRUTH.md) for where things live.

---

## Status Legend

| Symbol | Meaning |
|--------|---------|
| `[ ]` | Not started |
| `[~]` | In progress |
| `[x]` | Completed |
| `[—]` | Deferred / Won't do |

---

## P0 — Must Do Before Production

These block any real deployment. Ordered by dependency.

### P0-1: Install tiktoken in dependencies
- **Status**: `[x]`
- **Effort**: 5 min
- **Files**: `pyproject.toml`
- **What**: Add `tiktoken>=0.7.0` to `[project.dependencies]`
- **Why**: Without it, token counting uses `len(text) // 4` — a 20-30% error margin
- **Verify**: `.venv\Scripts\python.exe -c "import tiktoken; print(tiktoken.encoding_for_model('gpt-4o').encode('hello'))"`
- **GUIDE2 ref**: §2.4, §10

### P0-2: Fix `process()` / `_process_after_routing()` duplication
- **Status**: `[x]`
- **Effort**: 1 hour
- **Files**: `src/orchestrator/engine.py`
- **What**: Make `process()` compute routing then delegate to `_process_after_routing()` — eliminate ~30 lines of duplicate pipeline logic
- **Why**: Any pipeline fix/feature must be applied in two places today
- **Verify**: `pytest tests/test_orchestrator.py -v` (all existing tests pass)
- **GUIDE2 ref**: §2.2

### P0-3: Fix `_governance._budget_manager` encapsulation leak
- **Status**: `[x]`
- **Effort**: 30 min
- **Files**: `src/governance/guardian.py`, `src/orchestrator/engine.py`
- **What**: Add `GovernanceGuardian.count_tokens(text)` public method. Replace `self._governance._budget_manager.count_tokens(...)` calls in engine.py
- **Why**: Engine reaches through private attributes of Guardian — breaks encapsulation
- **Verify**: `pytest tests/test_governance.py tests/test_orchestrator.py -v`
- **GUIDE2 ref**: §2.3

### P0-4: Count tokens once per dispatch (not 3×)
- **Status**: `[x]`
- **Effort**: 1 hour
- **Files**: `src/orchestrator/engine.py`
- **What**: In `_dispatch()`, compute token count once and pass through budget check, governance check, and post-dispatch recording
- **Why**: Triple-counting wastes cycles and risks accounting drift between methods
- **Depends on**: P0-3 (clean access to token counting)
- **Verify**: `pytest tests/test_orchestrator.py tests/test_governance.py -v`
- **GUIDE2 ref**: §2.4

### P0-5: Wire LLM calls in `GenericAgent.execute()` — ✅ DONE
- **Status**: `[x]`
- **Effort**: 2-3 days
- **Files**: `src/llm/client.py` (new), `src/llm/__init__.py` (new), `src/agents/base.py`, `src/agents/generic.py`, `src/agents/plan_agent.py`, `src/agents/sub_plan_agent.py`, `src/agents/knowledge_base_agent.py`, `src/agents/log_analysis_agent.py`, `src/agents/remediation_agent.py`, `src/agents/security_sentinel_agent.py`, `src/orchestrator/engine.py`, `tests/test_llm.py` (new)
- **What**: All agents wired to Azure AI Foundry via `DefaultAzureCredential`. `_call_llm()` in `BaseAgent` delegates to singleton `LLMClient`. Graceful degradation — returns `None` when unconfigured.
- **Subtasks**:
  - `[x]` Choose SDK — `openai.AsyncAzureOpenAI` with `azure-identity`
  - `[x]` Wire credentials from `Settings` → `LLMClient` → agents
  - `[ ]` Implement streaming option (deferred to P1)
  - `[x]` Add timeout / retry for API calls (graceful `None` on any error)
  - `[x]` Update tests with mocked LLM responses (30 tests in `test_llm.py`)
- **Verify**: 450 tests passing (437 non-live + 13 live), `ruff check` + `ruff format` clean
- **GUIDE2 ref**: §2.1

---

## P1 — Should Do for Maintainability

### P1-6: Extract `bootstrap()` into builder pattern
- **Status**: `[x]`
- **Effort**: 2 hours
- **Files**: `src/main.py`
- **What**: Split `bootstrap()` into focused helper functions:
  - `_init_governance(settings) -> (guardian, selector, budget_mgr)`
  - `_create_orchestrator(...)`
  - `_register_agents(orchestrator, registry) -> dict`
  - `_load_skills(...)` + `_load_workflows(...)`
- **Why**: God function is hard to read, test, and extend. Returns a fragile 7-tuple.
- **Verify**: `python -m pytest tests/ -x -q --tb=short -m "not live"` (all tests pass)
- **GUIDE2 ref**: §2.5

### P1-7: Split `server.py` into route modules
- **Status**: `[x]`
- **Effort**: 2 hours
- **Files**: `src/server.py`, `src/server_models.py`, `src/server_routes/*.py`
- **What**: Refactor monolithic route definitions into domain registrars (`chat.py`, `core.py`, `workiq_plan.py`, `github.py`, `governance.py`, `system.py`) while keeping `from src.server import create_app` stable.
- **Why**: Monolithic server module caused navigation and merge-conflict friction.
- **Verify**: `pytest tests/ -v` + manual: `curl http://localhost:8080/health`
- **GUIDE2 ref**: §2.10

### P1-8: Eliminate double ForgeLoader in bootstrap
- **Status**: `[ ]`
- **Effort**: 30 min
- **Files**: `src/main.py`
- **What**: Make context config loading a static/class method or load config before constructing the loader (single instantiation)
- **Why**: `ForgeLoader` is constructed twice — once for context config, then again with governance attached
- **Verify**: `pytest tests/test_forge.py -v`
- **GUIDE2 ref**: §2.11

### P1-9: Add ConversationContext history limit
- **Status**: `[x]`
- **Effort**: 30 min
- **Files**: `src/orchestrator/context.py`
- **What**: Add `max_history` parameter to `add_user_message()`. Trim `self.messages` when it exceeds the limit.
- **Why**: Messages list grows without bound in long sessions
- **Verify**: Add test for history trimming in `tests/test_orchestrator.py`
- **GUIDE2 ref**: §2.8

### P1-10: Move `ContextWindowExceededError` import to top of engine.py
- **Status**: `[x]`
- **Effort**: 5 min
- **Files**: `src/orchestrator/engine.py`
- **What**: Move the `from src.governance.guardian import ContextWindowExceededError` from inside `_dispatch()` to the top-level imports
- **Why**: Late import inside method body is non-standard and confusing
- **Verify**: `pytest tests/test_orchestrator.py -v`
- **GUIDE2 ref**: §2.15

---

## P2 — Should Do for Robustness

### P2-11: Add retry / circuit-breaker for LLM calls
- **Status**: `[ ]`
- **Effort**: 1 day
- **Files**: new `src/resilience.py`, `src/agents/generic.py`
- **What**: Create `@retry(max_attempts=3, backoff=exponential)` decorator. Add circuit-breaker state (open after N consecutive failures). Apply to LLM call sites and WorkIQ subprocess calls.
- **Depends on**: P0-5 (LLM calls must exist to wrap)
- **GUIDE2 ref**: §2.12

### P2-12: Add input sanitisation layer
- **Status**: `[x]`
- **Effort**: 2 hours
- **Files**: new `src/orchestrator/input_guardrails.py`, `src/orchestrator/engine.py`, `src/config.py`
- **What**: `sanitize_user_message(...)` — strip control chars, enforce configurable length limit, and detect prompt-injection heuristics. Called in `process()` and `process_with_enrichment()` before routing.
- **Why**: When LLM calls are wired, unsanitised input is a prompt injection surface
- **GUIDE2 ref**: §2.14

### P2-13: Define SelectorProtocol ABC
- **Status**: `[~]`
- **Effort**: 1 hour
- **Files**: new `src/orchestrator/protocols.py`, update `src/orchestrator/plan_selector.py`, `src/governance/selector.py`, `src/workiq/selector.py`
- **What**: Create `SelectorProtocol` with `wait_for_review(request_id)` and `cleanup(request_id)`. Make all selectors implement it.
- **Why**: Three selectors share the same pattern but no interface — adding a fourth requires reverse-engineering the contract.
- **Progress**: Shared timeout contract extracted to `src/orchestrator/hitl_utils.py::wait_for_resolution()` and wired across selectors; protocol typing still pending.
- **GUIDE2 ref**: §2.9

### P2-14: Dynamic `python_class` import in manifests
- **Status**: `[ ]`
- **Effort**: 2 hours
- **Files**: `src/forge/loader.py`, `src/main.py`, agent YAML manifests
- **What**: Add `python_class: src.agents.log_analysis_agent.LogAnalysisAgent` to `agent.yaml`. Loader dynamically imports and constructs. Remove `_SPECIALISED_CLASSES` dict from `main.py`.
- **Why**: Eliminates Open/Closed violation — new agent = new YAML + new .py, no edits to main.py
- **GUIDE2 ref**: §2.6

### P2-15: Settings dependency injection (remove singleton)
- **Status**: `[ ]`
- **Effort**: 2 hours
- **Files**: `src/config.py`, all files that call `get_settings()`
- **What**: Accept `Settings` as a constructor parameter everywhere. Keep `get_settings()` as default. Tests pass their own `Settings(...)` instead of monkeypatching.
- **GUIDE2 ref**: §2.13

---

## P3 — Nice to Have

### P3-16: Auto-generate `AgentType` enum from `_registry.yaml`
- **Status**: `[ ]`
- **Effort**: 1 hour
- **Files**: `src/orchestrator/router.py`, `forge/_registry.yaml`
- **What**: At import time, read `_registry.yaml` and generate `AgentType` members dynamically. Eliminates enum drift.
- **GUIDE2 ref**: §2.7

### P3-17: Smart aggregation (dedup, conflict resolution)
- **Status**: `[ ]`
- **Effort**: 1 day
- **Files**: `src/orchestrator/engine.py`
- **What**: In `_aggregate()`, detect overlapping content from fan-out agents, deduplicate, and resolve conflicts (e.g., contradictory recommendations from security vs. remediation).
- **GUIDE2 ref**: §13

### P3-18: Summarize truncation strategy (LLM-based)
- **Status**: `[ ]`
- **Effort**: 1 day
- **Files**: `src/forge/context_budget.py`
- **What**: Implement `_truncate_summarize()` to call the LLM for a summary when input exceeds budget, instead of falling back to `priority` truncation.
- **Depends on**: P0-5 (LLM calls available)
- **GUIDE2 ref**: §2.1 (stub list)

### P3-19: Integration tests with real LLM — ✅ DONE
- **Status**: `[x]`
- **Effort**: 2 days
- **Files**: `tests/test_llm_live.py` (new)
- **What**: 13 end-to-end tests that call real Azure OpenAI (gated by `@pytest.mark.live` + `AZURE_AI_FOUNDRY_ENDPOINT` env var). Validates: simple chat, multi-turn, agent `_call_llm()`, `DefaultAzureCredential` token acquisition, pipeline routing, error resilience.
- **Azure resource**: `protoforge-openai` in `protoforge-rg` (eastus2), model `gpt-5.2-chat`, `DefaultAzureCredential` auth.
- **Run**: `pytest tests/test_llm_live.py -m live -v` (requires `az login`)
- **Depends on**: P0-5 ✔
- **GUIDE2 ref**: §13

### P3-20: Agent health dashboards (Grafana / AppInsights)
- **Status**: `[ ]`
- **Effort**: 1 day
- **Files**: new `dashboards/`
- **What**: Pre-built dashboard JSON/ARM templates showing agent latency, token usage, error rates, governance alerts. Connect to OpenTelemetry exporter.
- **GUIDE2 ref**: §11

---

## Completion Log

Track completed items here with date and commit hash.

| Date | Item | Commit | Notes |
|------|------|--------|-------|
| 2026-02-23 | P0-1 | `4d5128c` | Added tiktoken>=0.7.0 to pyproject.toml |
| 2026-02-23 | P0-2 | `4d5128c` | process() delegates to _process_after_routing() |
| 2026-02-23 | P0-3 | `4d5128c` | GovernanceGuardian.count_tokens() public method |
| 2026-02-23 | P0-4 | `4d5128c` | Single token count in _dispatch() |
| 2026-02-23 | P1-9 | `4d5128c` | max_history=200 on ConversationContext |
| 2026-02-23 | P1-10 | `4d5128c` | ContextWindowExceededError import at module top |
| 2026-02-23 | Lifecycle HITL | — | Agent disable/remove with HITL confirmation, 30 new tests (363 total) |
| 2026-02-24 | P0-5 | `218eb33` | LLM wiring — all agents call `_call_llm()`, 30 mocked tests (408 total) |
| 2026-02-24 | P3-19 | — | 13 live integration tests with real Azure OpenAI (`gpt-5.2-chat`), `DefaultAzureCredential` (421 total) |
| 2026-03-06 | P2-12 | — | Added input guardrails module + engine wiring + config flags + tests |
| 2026-03-06 | P1-7 | — | Split HTTP layer into `src/server_routes/*` with shared `src/server_models.py`; kept `create_app()` API stable |

---

*Last updated: 2026-03-06 — ProtoForge v0.1.1*
