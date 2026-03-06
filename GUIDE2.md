# GUIDE2 — Maintaining ProtoForge & Tuning Subagents

> **TL;DR for LLMs**: Maintenance & tuning guide (900+ lines / 13 sections).
> Covers: architecture reality check, DE critique, budget tuning, routing tuning,
> governance tuning, prompt engineering, debugging, observability, runbook.
>
> This is doc **9 of 10** in the reading order. Read
> [ARCHITECTURE.md](ARCHITECTURE.md) first for orientation.
> **See also**: [MAINTENANCE.md](MAINTENANCE.md) for the update protocol.

> **Audience**: Engineers maintaining the codebase, tuning token budgets,
> adding/removing agents, and operating ProtoForge in production.
>
> **Companion to**: [GUIDE.md](GUIDE.md) (architecture & developer reference)

---

## Table of Contents

1. [Architecture Reality Check](#1-architecture-reality-check)
2. [Distinguished Engineer Critique](#2-distinguished-engineer-critique)
3. [Tuning Per-Agent Token Budgets](#3-tuning-per-agent-token-budgets)
4. [Adding a New Subagent](#4-adding-a-new-subagent)
5. [Removing or Disabling a Subagent](#5-removing-or-disabling-a-subagent)
6. [Tuning Routing & Intent Classification](#6-tuning-routing--intent-classification)
7. [Tuning Governance Thresholds](#7-tuning-governance-thresholds)
8. [Tuning the Fan-Out Cap](#8-tuning-the-fan-out-cap)
9. [Prompt Engineering for Subagents](#9-prompt-engineering-for-subagents)
10. [Debugging Budget Enforcement](#10-debugging-budget-enforcement)
11. [Monitoring & Observability in Production](#11-monitoring--observability-in-production)
12. [Common Maintenance Runbook](#12-common-maintenance-runbook)
13. [Improvement Roadmap](#13-improvement-roadmap)

---

## 1. Architecture Reality Check

Before tuning anything, understand what the code *actually does* today vs.
what the docs describe.

### What works end-to-end (verified by 450 tests)

| Layer | Status | Notes |
|-------|--------|-------|
| Forge loader & manifest parsing | **Working** | Reads `agent.yaml`, resolves prompts/instructions |
| Keyword-based routing | **Working** | Regex scoring in `IntentRouter.route_by_keywords()` |
| Plan-first pipeline | **Working** | Plan → Sub-Plan → fan-out → aggregate |
| Budget allocation & truncation | **Working** | `allocate()` / `truncate()` / `fits_budget()` in dispatch |
| Governance hard cap | **Working** | `ContextWindowExceededError` raised at 128K, fail-closed |
| Fan-out cap | **Working** | Limited to `max_parallel_agents` (default 3) |
| HITL selectors (prepare/wait/resolve) | **Working** | Data structures flow; requires HTTP caller |
| WorkIQ enrichment pipeline | **Working** | 2-phase HITL; requires `workiq` CLI installed |

### What does NOT work yet

| Layer | Status | Impact |
|-------|--------|--------|
| **LLM inference** | **Working** | All agents call `_call_llm()` → Azure AI Foundry (`gpt-5.2-chat`) via `DefaultAzureCredential`. Graceful fallback to placeholder when unconfigured. 13 live integration tests in `test_llm_live.py`. |
| **LLM-based routing** | **Working** | `OrchestratorEngine._route_with_llm()` sends JSON intent-classification prompt, returns `RoutingDecision`. |
| **Summarize truncation strategy** | **Stub** | Falls back to `priority` truncation |
| **Token counting (tiktoken)** | **Available** | `tiktoken>=0.7.0` in `pyproject.toml`; falls back to `len(text) // 4` only if uninstalled |
| **`protoforge serve`** | **Broken** | Exit code 1 — likely missing env vars |

**Key takeaway**: The orchestration data-flow AND LLM intelligence are both
complete and tested. All agents call `_call_llm()` for real inference when
configured (Azure AI Foundry via `DefaultAzureCredential`), with graceful
fallback to placeholder responses when no endpoint is set. 450 tests total
(437 non-live + 13 live). Tuning settings below are now active.

---

## 2. Distinguished Engineer Critique

### 2.1 — CRITICAL: No actual intelligence — ✅ DONE

Every agent's `execute()` now calls `_call_llm()` (defined in `BaseAgent`)
which delegates to the singleton `LLMClient` in `src/llm/client.py`.

**Integration summary:**

- **Provider**: Azure AI Foundry via `openai.AsyncAzureOpenAI` + `DefaultAzureCredential` (`az login`). Also supports API-key auth and direct OpenAI.
- **Graceful degradation**: When no `AZURE_AI_FOUNDRY_ENDPOINT` is set, `_call_llm()` returns `None` and agents fall back to their existing placeholder responses. All 378 original tests pass without any LLM mock.
- **Pattern**: Each agent enriches the prompt with domain context (detected patterns, prior results, available sub-agents) before calling `_call_llm()`. LLM response → higher confidence score; fallback → lower confidence.
- **Engine routing**: `OrchestratorEngine._route_with_llm()` sends a JSON intent-classification prompt and parses the structured response into a `RoutingDecision`.
- **Tests**: 30 mocked tests in `tests/test_llm.py` + 13 live integration tests in `tests/test_llm_live.py`. Total: 450 tests.
- **Config**: `src/config.py` — `LLMConfig` with `azure_endpoint`, `azure_model`, `auth_method` (AZURE_DEFAULT / API_KEY), `active_provider` auto-detection.

**Files added/changed:**

| File | Change |
|---|---|
| `src/llm/__init__.py` | New — exports `LLMClient`, `get_llm_client` |
| `src/llm/client.py` | New — async LLM client, singleton, multi-provider |
| `src/agents/base.py` | Added `_call_llm()` method |
| `src/agents/generic.py` | Wired LLM with fallback |
| `src/agents/plan_agent.py` | Wired LLM with sub-agent enrichment |
| `src/agents/sub_plan_agent.py` | Wired LLM with plan-context enrichment |
| `src/agents/knowledge_base_agent.py` | Wired LLM with knowledge-source enrichment |
| `src/agents/log_analysis_agent.py` | Wired LLM with pattern-detection enrichment |
| `src/agents/remediation_agent.py` | Wired LLM with prior-result enrichment |
| `src/agents/security_sentinel_agent.py` | Wired LLM with security-category enrichment |
| `src/orchestrator/engine.py` | Added `_route_with_llm()` |
| `tests/test_llm.py` | New — 30 tests across 12 classes |

### 2.2 — CRITICAL: Code duplication in the pipeline — ✅ DONE (commit `4d5128c`)

`engine.py` had two near-identical pipelines. Fixed: `process()` now computes
routing then delegates to `_process_after_routing()`, eliminating duplication.

```python
async def process(self, user_message: str, *, ctx: ConversationContext | None = None) -> tuple[str, ConversationContext]:
    sanitized_message, request_ctx = self._prepare_request_context(user_message, ctx=ctx)
    if self._governance:
        self._governance.reset_run()  # task-local run state
    result = await self._process_standard_pipeline(sanitized_message, request_ctx)
    return result, request_ctx
```

### 2.3 — HIGH: Leaky abstraction boundaries — ✅ DONE (commit `4d5128c`)

`GovernanceGuardian.count_tokens()` is now a public method. `engine.py` calls
`self._governance.count_tokens(payload)` instead of reaching through
`self._governance._budget_manager`.

```python
def count_tokens(self, text: str) -> int:
    if self._budget_manager:
        return self._budget_manager.count_tokens(text)
    return max(1, len(text) // 4)
```

### 2.4 — HIGH: Token counting happens 3 times per dispatch — ✅ DONE (commit `4d5128c`)

Fixed: `_dispatch()` now counts tokens once, then passes `input_tokens`
through for budget check, governance check, and recording.

```python
input_tokens = self._budget_manager.count_tokens(effective_message)
# ... use input_tokens for budget check, governance check, and recording
```

### 2.5 — HIGH: `bootstrap()` is a ~180-line God Function

`main.py:bootstrap()` does forge loading, governance init, agent registration,
skill loading, workflow loading, catalog population, and app creation in one
function that returns a 7-tuple.

**Fix**: Extract into a `BootstrapBuilder` class or at minimum split into:
- `_init_governance(settings) -> (guardian, selector, budget_mgr)`
- `_register_agents(orchestrator, registry) -> dict`
- `_load_skills_and_workflows(settings, registry) -> (skills, workflows)`

### 2.6 — HIGH: `_SPECIALISED_CLASSES` violates Open/Closed

Adding a new agent requires editing the dictionary in `main.py` line ~55.
The forge system was designed to eliminate this, but the lookup table persists.

**Fix**: Move the class binding into `agent.yaml`:

```yaml
python_class: src.agents.log_analysis_agent.LogAnalysisAgent
```

Then `_create_agent_from_manifest()` does a dynamic import:

```python
module_path, cls_name = manifest.python_class.rsplit(".", 1)
cls = getattr(importlib.import_module(module_path), cls_name)
```

### 2.7 — MEDIUM: `AgentType` enum vs. plain `str` identity crisis

The codebase uses `AgentType.PLAN` in some places and `"plan"` in others.
The enum is a `StrEnum` so they're equivalent at runtime, but the inconsistency
obscures intent. Is the enum the canonical set, or is it just convenience?

**Recommendation**: Pick one:
- **A)** Delete the enum. Use string constants or the YAML IDs directly.
- **B)** Keep the enum but make it auto-generated from `_registry.yaml` at
  import time so it can't drift.

### 2.8 — MEDIUM: ConversationContext grows without bound — ✅ DONE (commit `4d5128c`)

Fixed: `ConversationContext` now has a `max_history=200` class field and trims
on `add_user_message()`. Memory is bounded to 200 messages by default.

```python
@dataclass
class ConversationContext:
    max_history: int = 200

    def add_user_message(self, content: str) -> None:
        self.messages.append(Message(role=MessageRole.USER, content=content))
        if len(self.messages) > self.max_history:
            self.messages = self.messages[-self.max_history :]
```

### 2.9 — MEDIUM: Selector timeout contract drift — ✅ PARTIALLY DONE

`PlanSelector`, `WorkIQSelector`, and `GovernanceSelector` previously implemented
timeout waiting logic independently. This increased drift risk in timeout,
cancellation, and auto-resolution behavior.

**Implemented fix**:
- Added `src/orchestrator/hitl_utils.py::wait_for_resolution()`
- Updated all three selectors to use the shared helper
- Preserved policy semantics (plan/workiq/context/skill fail-open; lifecycle fail-closed)

**Remaining improvement (optional)**: add a formal `SelectorProtocol` typing
layer for static contract checking across future selector implementations.

### 2.10 — MEDIUM: server.py monolith — ✅ DONE

The HTTP layer is now modularized:

- `src/server.py` — app composition (`create_app`), CORS/auth setup, route wiring
- `src/server_models.py` — shared request/response contracts
- `src/server_routes/` — domain route registrars:
  - `chat.py`
  - `core.py` (MCP/catalog/workflows)
  - `workiq_plan.py`
  - `github.py`
  - `governance.py`
  - `system.py`

This keeps endpoint behavior unchanged while reducing merge conflicts and
improving navigation.

### 2.11 — MEDIUM: ForgeLoader runs twice in bootstrap

`bootstrap()` creates `ForgeLoader` twice — once for pre-loading context
config (line ~87), then again with governance attached (line ~98). The first
loader scans the filesystem, then is discarded.

**Fix**: Make context config loading a static/class method, or load config
separately before constructing the loader.

### 2.12 — LOW: No retry / circuit-breaker

WorkIQ subprocess calls, and eventually LLM calls, have no retry logic.
A transient failure (network blip, rate limit) kills the entire pipeline.

**Fix**: Add a `@retry(max_attempts=3, backoff=exponential)` decorator
before LLM integration lands.

### 2.13 — LOW: Mutable singleton settings

`get_settings()` stores a module-level `_settings`. Multiple calls return
the same object, but there's no thread safety and no way for tests to
inject different settings without monkeypatching the global.

**Fix**: Accept `Settings` as a constructor parameter everywhere, with
`get_settings()` as the default. Tests pass their own `Settings(...)`.

### 2.14 — LOW: No input sanitization — ✅ DONE

User input now passes through `src/orchestrator/input_guardrails.py` before
routing and LLM calls.

**Implemented fix**:
- `sanitize_user_message()` strips control characters and normalizes line endings
- Enforces configurable input cap (`SERVER_MAX_USER_INPUT_CHARS`)
- Adds prompt-injection heuristics (`ignore previous instructions`, `system prompt`,
  guardrail bypass phrases, jailbreak markers)
- Stores guardrail metadata in `ConversationContext` (`input_guardrails`)
- Controlled by `SERVER_PROMPT_INJECTION_GUARD_ENABLED`

### 2.15 — LOW: Late import inside `_dispatch()` body — ✅ DONE (commit `4d5128c`)

`ContextWindowExceededError` import moved to the top of `engine.py`.

---

## 3. Tuning Per-Agent Token Budgets

### Where budgets are defined

| Source | File | Precedence |
|--------|------|------------|
| Agent-specific override | `forge/agents/<id>/agent.yaml` → `context_budget:` | **Highest** |
| Default by type | `forge/_context_window.yaml` → `defaults:` | Medium |
| Hard-coded fallback | `src/forge/context_budget.py` line ~80 (`16000`/`8000`) | Lowest |

### Budget math constraint

The global hard cap is **128,000 tokens**. The worst-case budget using actual
configured agent budgets is:

```
Plan (32K) + Sub-Plan (20K) + top 3 specialists (25K + 25K + 22K) = 124K
```

The 25K agents are `code_research` (17K+8K) and `knowledge_base` (17K+8K).
All other specialists use 22K (15K+7K) or less. Headroom to 128K cap: **4K**.

To verify your budget changes don't exceed this:

```
sum = plan_input + plan_output
    + sub_plan_input + sub_plan_output
    + (specialist_1_input + specialist_1_output)
    + (specialist_2_input + specialist_2_output)
    + (specialist_3_input + specialist_3_output)

assert sum <= 128000
```

### How to change a specialist's budget

Edit the agent's YAML manifest:

```yaml
# forge/agents/log_analysis/agent.yaml
context_budget:
  max_input_tokens: 18000    # was 15000
  max_output_tokens: 8000    # was 7000
  strategy: sliding_window   # or: priority, summarize
```

Then verify your math. If this agent's envelope (input + output) grew,
reduce another agent or lower `max_parallel_agents`.

### How to change the Plan Agent's budget

```yaml
# forge/plan/agent.yaml
context_budget:
  max_input_tokens: 28000    # was 24000
  max_output_tokens: 10000   # was 8000 (38K envelope)
```

The Plan Agent always runs, so its envelope directly reduces headroom.

### Strategy selection guide

| Strategy | When to use | Trade-off |
|----------|------------|-----------|
| `priority` | Most agents — keeps the beginning of the content | Loses tail (older context) |
| `sliding_window` | Log analysis, streaming data — keep most recent | Loses head (original question context) |
| `summarize` | *Not yet functional* — falls back to `priority` | Requires LLM call; adds latency |

### Validating budget changes

```bash
# Run the budget enforcement tests
.venv\Scripts\python.exe -m pytest tests/test_governance.py -k budget -v
```

---

## 4. Adding a New Subagent

> **Canonical source → [BUILDING_AGENTS.md](BUILDING_AGENTS.md)**
>
> The full 8-step tutorial — including forge manifest, routing keywords,
> optional Python class, tests, budget verification, and Azure AI Foundry
> LLM wiring — lives in BUILDING_AGENTS.md.

---

## 5. Removing or Disabling a Subagent

### Option A: Runtime disable via HITL (recommended) — ✅ IMPLEMENTED

Use the lifecycle HTTP endpoints to disable or remove agents at runtime
with human-in-the-loop confirmation. No restart required.

```bash
# 1. Disable an agent (triggers HITL review)
curl -X POST http://localhost:8080/agents/data_analysis/disable

# 2. Check pending review (shows which agents remain enabled)
curl http://localhost:8080/governance/lifecycle-reviews

# 3. Approve the disable action
curl -X POST http://localhost:8080/governance/lifecycle-reviews/resolve \
  -H "Content-Type: application/json" \
  -d '{"request_id": "<id from step 2>", "accepted": true}'

# 4. Re-enable later (no HITL required)
curl -X POST http://localhost:8080/agents/data_analysis/enable

# 5. Permanently remove (HITL-gated, same flow as disable)
curl -X DELETE http://localhost:8080/agents/data_analysis
```

**Key design decisions**:
- Disable/remove are **fail-CLOSED on timeout** — if the human doesn't
  respond within 120s, the action is rejected (unlike context/skill
  reviews which fail-open).
- `enable_agent()` has **no HITL gate** — re-enabling is always safe.
- On disable/remove: routing patterns deregistered + budget deallocated
  automatically via `IntentRouter.deregister_patterns()` and
  `ContextBudgetManager.deallocate()`.

**Endpoints**:
| Method | Path | HITL? |
|--------|------|-------|
| POST | `/agents/{id}/disable` | Yes — fail-CLOSED |
| POST | `/agents/{id}/enable` | No |
| DELETE | `/agents/{id}` | Yes — fail-CLOSED |
| GET | `/agents/enabled` | — |
| GET | `/agents/disabled` | — |
| GET | `/governance/lifecycle-reviews` | — |
| POST | `/governance/lifecycle-reviews/resolve` | — |

### Option B: Remove from forge/ entirely

```bash
rm -rf forge/agents/<agent_id>/
```

Remove from `_registry.yaml`. Remove from `_SPECIALISED_CLASSES` in `main.py`
if it had a custom class. Remove the `AgentType` enum member if one existed.

### Option C: Disable without removing (static)

Add a `disabled: true` field to the agent.yaml (requires adding support
in `ForgeLoader._load_agent_dir()`):

```yaml
id: my_agent
disabled: true   # loader skips this agent
```

### Option D: Remove from routing only

Remove the agent's tags/patterns from `_BUILTIN_KEYWORD_ROUTES` or its
`tags:` list. The agent remains registered but is never routed to.

---

## 6. Tuning Routing & Intent Classification

### How routing works today

1. **Keyword scoring** — Each agent has compiled regex patterns. Every
   pattern that matches the user message scores +1 for that agent.
2. **Ranking** — Agents are ranked by total score. Top scorer = primary.
   Next 2 scorers with score > 0 = secondary agents.
3. **Confidence** — `primary_score / total_score`. Below 0.5 triggers
   LLM fallback (currently a no-op).
4. **Default** — If no patterns match, routes to `knowledge_base`.

### Tuning keyword patterns

Edit `_BUILTIN_KEYWORD_ROUTES` in `src/orchestrator/router.py`:

```python
AgentType.LOG_ANALYSIS: [
    r"\blog[s]?\b",
    r"\berror\s*log\b",
    r"\bstack\s*trace\b",
    # Add more patterns to increase routing accuracy:
    r"\bkusto\b",           # catches KQL log queries
    r"\bapp\s*insights\b",  # catches Azure Monitor queries
],
```

**Tips**:
- More patterns = more granular scoring, but also more false positives
- Use `\b` word boundaries to avoid substring matches
- Test with: `python -c "from src.orchestrator.router import IntentRouter; r = IntentRouter(); print(r.route_by_keywords('your test message'))"`

### Tuning the default agent

Change `_DEFAULT_AGENT` in `router.py`:

```python
_DEFAULT_AGENT: str = AgentType.KNOWLEDGE_BASE  # change this
```

### Tuning WorkIQ keyword boost

When WorkIQ enrichment is active, hints boost agent scores by +1 each.
To increase the boost weight, modify `route_with_context()` in `router.py`:

```python
# Current: each hint = +1
scores[aid] += 1
# Increase to: each hint = +2
scores[aid] += 2
```

---

## 7. Tuning Governance Thresholds

### Context window thresholds

Edit `forge/_context_window.yaml`:

```yaml
governance:
  context_window:
    warning_threshold: 110000   # when to alert (HITL triggered)
    hard_cap: 128000            # when to ABORT (fail-closed)
    enforce_hard_cap: true      # false = warn only, true = raise exception
```

**When to adjust**:
- If agents frequently hit warnings at 110K, raise the threshold
- If you upgrade to a model with 200K context, raise both proportionally
- **Never** set `enforce_hard_cap: false` in production

### Skill cap

```yaml
governance:
  skill_cap:
    max_skills_per_agent: 4     # raise if agents legitimately need more
    allow_override: true        # human can accept violations via HITL
```

### HITL timeout

```yaml
governance:
  hitl:
    timeout_seconds: 120        # seconds before auto-resolve
    auto_resolve_action: accept # accept | reject
```

- Set `timeout_seconds: 30` for automated pipelines (fast fail-open)
- Set `timeout_seconds: 300` for interactive use (give humans time)
- Set `auto_resolve_action: reject` for stricter governance

---

## 8. Tuning the Fan-Out Cap

### Current setting

```yaml
# forge/_context_window.yaml
scaling:
  max_parallel_agents: 3    # max specialists per fan-out
```

### How fan-out interacts with budgets

Each specialist in the fan-out gets its own budget allocation. Worst case:

```
total = plan_envelope + sub_plan_envelope + (cap × max_specialist_envelope)
```

If you raise the cap from 3 to 4, you need to shrink per-agent budgets:

```yaml
# Example: cap=4, each specialist ≤ 19K envelope
scaling:
  max_parallel_agents: 4

# And reduce specialist budgets:
defaults:
  specialist:
    max_input_tokens: 12000
    max_output_tokens: 7000   # 19K envelope × 4 = 76K + 52K coordinators = 128K
```

### When to change

- **Raise to 4-5**: Queries that commonly need 4+ agents (rare)
- **Lower to 2**: Tighter budget, faster responses, less token spend
- **Keep at 3**: Best balance for most use cases

---

## 9. Prompt Engineering for Subagents

### Where prompts live

| Type | Path | Loaded by |
|------|------|-----------|
| Agent system prompt | `forge/agents/<id>/prompts/system.md` | `ForgeLoader` → `BaseAgent.from_manifest()` |
| Agent instructions | `forge/agents/<id>/instructions/*.md` | `ForgeLoader` (stored in `manifest.resolved_instructions`) |
| Shared prompts | `forge/shared/prompts/*.md` | `ForgeLoader` (stored in `registry.shared_prompts`) |
| Shared instructions | `forge/shared/instructions/*.md` | `ForgeLoader` (stored in `registry.shared_instructions`) |
| Fallback (no manifest) | Hardcoded in agent class (e.g., `_DEFAULT_PLAN_PROMPT`) | Agent constructor |

### Prompt structure best practices

```markdown
<!-- forge/agents/log_analysis/prompts/system.md -->

# Role Definition (always first — sets the agent's identity)
You are the Log Analysis Agent in the ProtoForge multi-agent system.

# Capabilities (what the agent CAN do)
You specialize in:
- Parsing structured and unstructured log data
- Identifying error patterns and anomalies
- Root cause analysis from stack traces

# Constraints (what the agent MUST NOT do)
- Do NOT attempt code fixes — delegate to the Remediation Agent
- Do NOT exceed 3 log files per analysis
- Always cite the log line number

# Output Format (how to structure responses)
Structure your response as:
1. **Summary**: One-paragraph diagnosis
2. **Evidence**: Relevant log entries with line numbers
3. **Root Cause**: Most likely cause with confidence level
4. **Recommendation**: Next steps (which agent to invoke)

# Context Awareness (what upstream agents provide)
The Plan Agent may have set `plan_output` in working memory with a
strategic context. Reference it when available.
```

### Common prompt anti-patterns

| Anti-pattern | Problem | Fix |
|-------------|---------|-----|
| Prompt > 4000 tokens | Eats into input budget | Trim to essentials |
| "You can do anything" | Agent lacks focus | Define explicit boundaries |
| No output format spec | Inconsistent responses | Add structured format |
| No mention of other agents | Agent doesn't know how to delegate | Add delegation rules |
| Copy-pasted from another agent | Wrong domain knowledge | Write domain-specific |

### Testing prompt changes

After editing a prompt, verify the manifest still loads:

```bash
.venv\Scripts\python.exe -c "
from src.forge.loader import ForgeLoader
registry = ForgeLoader('forge').load()
m = registry.agents.get('log_analysis')
print(f'Prompt length: {len(m.resolved_prompts.get(\"system\", \"\"))} chars')
"
```

---

## 10. Debugging Budget Enforcement

### Enable debug logging

```bash
LOG_LEVEL=DEBUG .venv\Scripts\python.exe -m src.main serve
```

Look for these structured log events:

| Event | Meaning |
|-------|---------|
| `budget_allocated` | Budget successfully allocated for agent |
| `input_truncated` | Input was too large, truncated to fit |
| `context_window_warning` | Cumulative tokens crossed warning threshold |
| `context_window_hard_cap` | Hard cap exceeded — dispatch aborted |
| `dispatch_aborted_hard_cap` | Agent execution was blocked |
| `fan_out_cap_enforced` | Too many agents requested, some dropped |

### Inspecting budget state at runtime

```python
# In a debug session or test:
budget_mgr = orchestrator._budget_manager
print(budget_mgr.usage_report())

# Governance state:
guardian = orchestrator._governance
print(guardian.governance_report())
```

### Common budget issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| Agent always truncated | `max_input_tokens` too low for typical queries | Raise in `agent.yaml` |
| Hard cap hit on 2nd agent | Plan Agent used too many tokens | Lower plan budget or raise hard cap |
| No truncation happening | `allocate_on_dispatch: false` in config | Set to `true` |
| Tokens count as 0 | tiktoken not installed, `len("") // 4 = 0` | Install tiktoken: `pip install tiktoken` |

---

## 11. Monitoring & Observability in Production

### Health check endpoint

```
GET /health → {"status": "healthy", ...}
```

### Governance status endpoint

```
GET /governance/status → {
  "cumulative_tokens": 45000,
  "hard_cap": 128000,
  "utilisation_pct": 35.2,
  "agent_usage": {"plan": 25000, "log_analysis": 20000},
  "unresolved_alerts": 0
}
```

`cumulative_tokens` and `agent_usage` are scoped to a single orchestration run
(task-local), preventing counter collisions across concurrent requests.

### Key metrics to monitor

| Metric | Source | Alert threshold |
|--------|--------|----------------|
| Cumulative tokens per run | `governance_report()["cumulative_tokens"]` | > 100K |
| Utilisation % | `governance_report()["utilisation_pct"]` | > 85% |
| Unresolved alerts | `governance_report()["unresolved_alerts"]` | > 0 |
| Agent error rate | `AgentRegistration.error_rate` | > 5% |
| Agent latency | `AgentResult.duration_ms` | > 30s |
| Truncation frequency | Count `input_truncated` log events | > 20% of dispatches |

### Control-plane threat model (HTTP)

| Threat | Surface | Mitigation in code | Operational control |
|---|---|---|---|
| Unauthorized governance/admin action | `/governance/*`, `/agents/*`, `/workflows/run`, `/github/*`, `/reviews/pending`, `POST /plan/accept`, `POST /sub-plan/accept`, `POST /workiq/select`, `POST /workiq/accept-hints` | Optional API-key guard in `server.py` | Set `SERVER_REQUIRE_CONTROL_PLANE_API_KEY=true` + strong `SERVER_CONTROL_PLANE_API_KEY` |
| Browser-origin abuse | Cross-origin access to control-plane endpoints | CORS middleware with wildcard+credentials safety downgrade | Set explicit `SERVER_CORS_ALLOWED_ORIGINS` in production |
| Cross-request status leakage | `/chat/status/{task_id}` phase reporting | Task-scoped `ConversationContext` in `_chat_tasks` (no global context dependency) | Keep task TTL short (`_CHAT_TASK_TTL`) and avoid long-lived sensitive state |
| Prompt-injection style input | Any `/chat` or `/chat/enriched` message | `sanitize_user_message()` heuristics + length cap + control-char stripping | Monitor `input_guardrails_triggered` and review false positives/true positives |

### OpenTelemetry integration

Configure in `.env`:

```env
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
LOG_LEVEL=INFO
SERVER_REQUIRE_CONTROL_PLANE_API_KEY=true
SERVER_CORS_ALLOWED_ORIGINS=https://your-admin-ui.example
SERVER_CORS_ALLOW_CREDENTIALS=true
```

The `structlog` events are already structured — pipe to your OTLP collector
for dashboard visibility.

---

## 12. Common Maintenance Runbook

### Task: Update the LLM model

1. Edit `.env` or `src/config.py`:
   ```env
   ANTHROPIC_MODEL=claude-opus-5.0
   ```
2. If the new model has a different context window, update:
   - `forge/_context_window.yaml` → `global.max_total_tokens`
   - `governance.context_window.hard_cap`
   - All per-agent budgets proportionally

### Task: Upgrade Python version

1. Ensure pydantic-core has a pre-built wheel for the target Python.
   **Python 3.14** currently requires Rust to build from source.
2. Recommended: stay on Python 3.12.x until pydantic-core ships 3.14 wheels.
3. Rebuild venv: `.venv\Scripts\python.exe -m pip install -e ".[dev]"`
4. Run full test suite: `.venv\Scripts\python.exe -m pytest`

### Task: Agent producing poor results

1. Check which prompt is being used:
   ```python
   from src.forge.loader import ForgeLoader
   r = ForgeLoader("forge").load()
   agent = r.agents["problematic_agent"]
   print(agent.resolved_prompts["system"])
   ```
2. Check if input is being truncated (look for `input_truncated` logs)
3. Check the routing — is the right agent being selected?
   ```python
   from src.orchestrator.router import IntentRouter
   r = IntentRouter()
   print(r.route_by_keywords("the problematic query"))
   ```
4. Tune the prompt in `forge/agents/<id>/prompts/system.md`
5. Tune the budget if truncation is the issue

### Task: Harden production control plane

1. Enable control-plane auth:
   ```env
   SERVER_REQUIRE_CONTROL_PLANE_API_KEY=true
   SERVER_CONTROL_PLANE_API_KEY=<long-random-secret>
   ```
2. Lock CORS to known operator origins:
   ```env
   SERVER_CORS_ALLOWED_ORIGINS=https://admin.example.com,https://ops.example.com
   SERVER_CORS_ALLOW_CREDENTIALS=true
   ```
3. Configure input guardrails for your risk profile:
   ```env
   SERVER_MAX_USER_INPUT_CHARS=64000
   SERVER_PROMPT_INJECTION_GUARD_ENABLED=true
   ```
4. Verify:
   - `GET /governance/status` returns `401` without `X-API-Key`
   - `POST /plan/accept` and `POST /sub-plan/accept` return `401` without `X-API-Key`
   - `POST /workiq/select` and `POST /workiq/accept-hints` return `401` without `X-API-Key`
   - `/health` remains open
   - logs show expected `input_guardrails_triggered` events on adversarial prompts

### Task: Add a new HITL gate

Follow the selector pattern (see `AgentLifecycleReview` in `selector.py` for
a complete worked example):
1. Create dataclass for the review request
2. Create a Selector class with `prepare_*()`, `resolve_*()`, `wait_for_*()`
3. Wire into engine.py at the appropriate pipeline stage
4. Add HTTP endpoints in `src/server_routes/` and wire the registrar in `create_app()`
5. Add tests

### Task: Reset everything for a fresh session

```python
orchestrator.reset_context()  # clears ConversationContext + governance counters
```

Via HTTP:
```
POST /chat {"message": "...", "session_id": null}
```

---

## 13. Improvement Roadmap

> **Canonical source**: See [TODO.md](TODO.md) for the full prioritised backlog
> (P0-P3, 20 items, status tracking, completion log).
>
> This section previously duplicated the roadmap. It now cross-references
> the single source of truth to avoid drift.

**Quick status** (updated 2026-03-06):
- **P0**: 5/5 done
- **P1**: 3/5 done
- **P2**: 1/5 done
- **P3**: 0/5

---

*Last updated: 2026-03-06 — ProtoForge*
