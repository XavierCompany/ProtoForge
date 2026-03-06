# ProtoForge Architecture

> **Compact reference for LLMs and developers.**
> ~250 lines — fits inside a single context window read.
> See `.github/copilot-instructions.md` for the shortest orientation.

---

## 1. System Overview

ProtoForge is a **plan-first multi-agent orchestrator**. Every user request:

1. Gets classified by `IntentRouter` (keyword patterns + optional LLM)
2. Goes to **Plan Agent** first (always) — produces a strategy
3. Passes through a **HITL gate** — user reviews the plan
4. Goes to **Sub-Plan Agent** — plans prerequisite resources
5. Passes through a second **HITL gate** — user reviews resources
6. Fans out to **≤3 specialist agents** in parallel
7. Results are aggregated and returned

Token budget: `Plan(32K) + SubPlan(20K) + 3×Specialist(≤25K) ≤ 128K cap`

---

## 2. Module Dependency Graph

```
             src/server.py (FastAPI composition)
              ├── src/server_models.py
              └── src/server_routes/*.py
                         │
                    src/main.py (bootstrap)
                    ┌────┼────────────────┐
                    │    │                │
          src/orchestrator/         src/governance/
          ├── engine.py ◄────────── guardian.py
          ├── router.py             selector.py
          ├── context.py
          ├── plan_selector.py
          ├── hitl_utils.py
          └── input_guardrails.py
                    │
              src/agents/
              ├── base.py (ABC)
              ├── plan_agent.py
              ├── sub_plan_agent.py
              └── *_agent.py (8 specialist files)
                    │
              src/forge/              src/mcp/
              ├── loader.py           ├── server.py
              ├── context_budget.py   ├── protocol.py
              └── contributions.py    └── skills.py
                    │
              src/workiq/             src/registry/
              ├── client.py           ├── catalog.py
              └── selector.py         └── workflows.py
```

**Import direction**: server + server_routes → main → orchestrator → agents → forge/governance.
Never import upward (e.g., agents must not import from orchestrator).

---

## 3. Package Public APIs

### `src/orchestrator/`
- `OrchestratorEngine` — top-level pipeline:
  `process(user_message, *, ctx=None)`, `process_with_enrichment(user_message, *, ctx=None)`
- `IntentRouter` — `route_by_keywords()`, `route_with_context()`, keyword + enrichment routing
- `ConversationContext` — shared state: messages, metadata, history
- `AgentResult` — dataclass: agent_id, content, confidence, artifacts, duration_ms
- `PlanSelector` — Plan/Sub-Plan HITL gate:
  `prepare_plan_review()`, `resolve_plan_review()`,
  `prepare_resource_review()`, `resolve_resource_review()`
- `sanitize_user_message()` — request guardrails before routing/LLM:
  control-char stripping, input-length cap, and prompt-injection heuristics

### `src/agents/`
- `BaseAgent` (ABC) — `execute(message, context, params) → AgentResult`, `from_manifest()`, `_call_llm(messages)`
- 10 implementations: plan, sub_plan, log_analysis, code_research, remediation,
  knowledge_base, data_analysis, security_sentinel, github_tracker, workiq
- `code_research` and `data_analysis` have no dedicated Python class — they use `GenericAgent.from_manifest()`

### `src/llm/`
- `LLMClient` — async multi-provider LLM client: `chat(messages) → str | None`
- `get_llm_client()` — singleton factory (lazy init)
- Providers: Azure AI Foundry (`DefaultAzureCredential` or API key), OpenAI, Anthropic, Google
- Graceful degradation: returns `None` on any error or when unconfigured

### `src/governance/`
- `GovernanceGuardian` — `check_context_window()`, `record_agent_usage()`, `audit_manifest()`
- `GovernanceSelector` — context/skill/lifecycle HITL:
  `prepare_*_review()`, `wait_for_*_review()`, `resolve_*_review()`
- `ContextWindowExceededError` — raised at 128K hard cap

### `src/forge/`
- `ForgeLoader` — `load()` → `ForgeRegistry` with agent manifests + skills
- `ContextBudgetManager` — `allocate()`, `truncate()`, per-agent token budgets
- `ContributionManager` — CRUD for dynamic skills/prompts/workflows

### `src/mcp/`
- `MCPServer` — `handle_request()`, JSON-RPC tools/resources/prompts
- `SkillLoader` — discovers skills from `forge/` YAML

### `src/workiq/`
- `WorkIQClient` — `ask()` → M365 context query
- `WorkIQSelector` — 2-phase HITL: content selection + keyword hints

### `src/registry/`
- `AgentCatalog` — `list_agents()`, `search()`, metadata lookup
- `WorkflowEngine` — `list_workflows()`, `run_workflow()`

---

## 4. Forge Ecosystem (Declarative Config)

```
forge/
  _registry.yaml          # Agent registry — source of truth for IDs
  _context_window.yaml    # Token budgets, governance thresholds, strategies
  agents/<id>/
    agent.yaml            # Manifest: id, name, type, skills, budget
    instructions/*.md     # Agent-specific instructions
    prompts/*.md          # Agent-specific prompts
    skills/*.yaml         # Agent-specific skill definitions
  plan/
    agent.yaml            # Plan Agent manifest
    instructions/         # coordination.md, routing_rules.md
    prompts/              # decomposition.md, routing.md
    workflows/            # Plan-level workflow definitions
  shared/                 # Shared prompts and workflows
  contrib/                # Dynamic contributions (audit_log.yaml)
```

**Canonical ownership**: `forge/agents/<id>/agent.yaml` is the single source
of truth for agent identity. All other locations (router enum, main.py dict,
registry.yaml) are derived. See SOURCE_OF_TRUTH.md for the full ownership map.

---

## 5. HITL (Human-in-the-Loop) Pattern

All 5 HITL gates follow the same pattern:

```python
# 1. Prepare review object
request_id = str(uuid.uuid4())
review = selector.prepare_plan_review(request_id, plan_content, recommended_agents)

# 2. Store pending — exposed via GET endpoint
# (selector stores pending request internally, keyed by request_id)

# 3. Wait with timeout (120s default)
review = await selector.wait_for_plan_review(request_id)

# 4. Auto-resolve on timeout
#    - Plans/Sub-Plans: fail-OPEN (auto-accept)
#    - WorkIQ + governance context/skill: fail-OPEN (auto-accept)
#    - Lifecycle (disable/remove): fail-CLOSED (auto-reject)
```

Timeout waiting is centralized through
`src/orchestrator/hitl_utils.py::wait_for_resolution()`, so Plan, WorkIQ, and
Governance selectors share one timeout/cancellation behavior path.

| Gate | Trigger | Auto-resolve | HTTP Endpoints |
|------|---------|-------------|----------------|
| Plan | Every request | accept | GET /plan/pending, POST /plan/accept |
| Sub-Plan | After plan accepted | accept | GET /sub-plan/pending, POST /sub-plan/accept |
| Context Window | Tokens > 110K | accept | GET+POST /governance/context-reviews/* |
| Skill Cap | Agent > 4 skills | accept | GET+POST /governance/skill-reviews/* |
| Lifecycle | disable/remove request | **reject** | GET+POST /governance/lifecycle-reviews/* |

---

## 6. Context Window Governance

Defined in `forge/_context_window.yaml`, enforced by `GovernanceGuardian`:

| Parameter | Value | Purpose |
|-----------|-------|---------|
| Hard cap | 128,000 tokens | Absolute maximum per orchestration run |
| Warning threshold | 110,000 tokens | Triggers HITL context review |
| Plan envelope | 32,000 tokens | Reserved for Plan Agent |
| Sub-Plan envelope | 20,000 tokens | Reserved for Sub-Plan Agent |
| Specialist cap | 25,000 tokens | Max per specialist agent |
| Max fan-out | 3 agents | Max concurrent specialists |
| Token counting | tiktoken (cl100k_base) | Falls back to len/4 estimate |

Budget math: `32K + 20K + 3×25K = 127K ≤ 128K` (1K headroom for aggregation)

---

## 7. Configuration

All config lives in `src/config.py` as Pydantic `BaseSettings`:

- `LLMConfig` — provider selection (Azure/OpenAI/Anthropic/Google), endpoints, API keys, models, auth method (`DefaultAzureCredential` or API key), `active_provider` auto-detection
- `ServerConfig` — host/port, control-plane API key guard, CORS policy, and
  input guardrail settings (`SERVER_MAX_USER_INPUT_CHARS`,
  `SERVER_PROMPT_INJECTION_GUARD_ENABLED`)
- `MCPConfig` — port, skills directory for MCP server
- `ForgeConfig` — path to forge/ directory
- `ObservabilityConfig` — OTLP endpoint, log level

Settings are loaded from `.env` file. Access via `get_settings()` singleton.

---

## 8. Testing

- **444 tests** across 12 test files, all async (`pytest-asyncio`).
- Fixtures in `tests/conftest.py` — pre-built engine, agents, guardian, router.
- Test files map 1:1 to source domains (test_orchestrator, test_governance, etc.)
- Live integration tests: `pytest -m live` (13 tests, requires `az login` + Azure endpoint)
- CI: GitHub Actions, Python 3.11 + 3.12 matrix, ruff lint + format + pytest.

---

## 9. Common Tasks Quick Reference

| Task | Key files to modify |
|------|-------------------|
| Add a new agent | `forge/agents/<id>/agent.yaml`, `src/agents/<id>_agent.py`, `AgentType` enum in `router.py`, `_SPECIALISED_CLASSES` in `main.py` |
| Change token budgets | `forge/_context_window.yaml` or `forge/agents/<id>/agent.yaml` — recalculate sum! |
| Add an HTTP endpoint | `src/server_routes/*.py` (route logic) + `src/server.py` (`create_app()` composition) |
| Add a HITL gate | Follow pattern in `src/governance/selector.py` or `src/orchestrator/plan_selector.py` |
| Add a skill | `forge/agents/<id>/skills/<name>.yaml` |
| Change routing | `src/orchestrator/router.py` — add keyword patterns to `AgentType` |

---

## 10. LLM Documentation Reading Order

See `.github/copilot-instructions.md` "Documentation Reading Order" section for
the canonical 10-file reading list with line counts. Total: ~6 270 lines (~65K tokens).

### GUIDE.md Section Index (navigation aid)

GUIDE.md is the largest doc at ~2760 lines. Read it fully, but use this index
to locate specific sections quickly:

| § | Title | Lines | Covers |
|---|-------|------:|--------|
|---|-------|------:|------------------------|
| 1 | Why This Architecture? | ~53 | Understand the plan-first rationale |
| 2 | Plan-First Design | ~98 | Understand engine.py pipeline flow |
| 3 | Architecture Design & Flow | ~105 | See the full pipeline diagram |
| 4 | Context Window Management | ~243 | Adjust token budgets or understand truncation |
| 5 | Splitting Tasks | ~115 | Decide: agent vs skill vs sub-agent |
| 6 | Governance Guardian | ~136 | Understand enforcement pillars |
| 7 | The Forge Ecosystem | ~154 | Work with forge/ YAML manifests |
| 8 | Agent Registry / Catalog | ~158 | Manage agent registration and skills |
| 9 | Expanding Plan Agent | ~91 | Enhance the coordinator agent |
| 10 | Expanding Sub-Agent | ~156 | Add capabilities to specialist agents |
| 11 | Adding a Brand-New Agent | ~247 | Step-by-step new agent creation |
| 12 | Adding Skills & Workflows | ~63 | Add YAML skill/workflow definitions |
| 13 | Dynamic Contributions | ~67 | Runtime CRUD for skills/agents |
| 14 | Sub-Plan Agent | ~130 | Dual HITL resource planning pipeline |
| 15 | WorkIQ Integration | ~294 | M365 enrichment, 2-phase HITL |
| 16 | GitHub Copilot CLI | ~45 | Dev workflow with Copilot |
| 17 | Multi-Model Code Review | ~246 | Run multiple LLMs in parallel for review |
| 18 | Architecture Decision Records | ~95 | Historical design decisions (13 ADRs) |
| 19 | Pre-Router Enrichment Source | ~216 | Add new data sources (Jira, Slack, etc.) |
