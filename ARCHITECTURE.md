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

Token budget: `Plan(32K) + SubPlan(20K) + 3×Specialist(≤25K) = 127K ≤ 128K cap`

---

## 2. Module Dependency Graph

```
                    src/server.py (FastAPI HTTP)
                         │
                    src/main.py (bootstrap)
                    ┌────┼────────────────┐
                    │    │                │
          src/orchestrator/         src/governance/
          ├── engine.py ◄────────── guardian.py
          ├── router.py             selector.py
          ├── context.py
          └── plan_selector.py
                    │
              src/agents/
              ├── base.py (ABC)
              ├── plan_agent.py
              ├── sub_plan_agent.py
              └── *_agent.py (7 specialists)
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

**Import direction**: server → main → orchestrator → agents → forge/governance.
Never import upward (e.g., agents must not import from orchestrator).

---

## 3. Package Public APIs

### `src/orchestrator/`
- `OrchestratorEngine` — top-level pipeline: `process()`, `process_with_enrichment()`
- `IntentRouter` — `route()`, `route_with_context()`, keyword + LLM classification
- `ConversationContext` — shared state: messages, metadata, history
- `AgentResult` — dataclass: agent_id, content, tokens_used, metadata
- `PlanSelector` — Plan HITL gate: `prepare_review()`, `resolve()`

### `src/agents/`
- `BaseAgent` (ABC) — `execute(context) → AgentResult`, `from_manifest()`
- 9 implementations: plan, sub_plan, log_analysis, code_research, remediation,
  knowledge_base, data_analysis, security_sentinel, github_tracker, workiq

### `src/governance/`
- `GovernanceGuardian` — `check_budget()`, `enforce_hard_cap()`, `audit_manifest()`
- `GovernanceSelector` — `request_disable()`, `request_unregister()`, lifecycle HITL
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
review = selector.prepare_review(data)

# 2. Store pending — exposed via GET endpoint
pending_reviews[review.id] = review

# 3. Wait with timeout (120s default)
result = await asyncio.wait_for(review.event.wait(), timeout=120)

# 4. Auto-resolve on timeout
#    - Plans/Sub-Plans: fail-OPEN (auto-accept)
#    - Lifecycle (disable/remove): fail-CLOSED (auto-reject)
```

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

Budget math: `32K + 20K + 3×25K = 127K ≤ 128K` (4K headroom for aggregation)

---

## 7. Configuration

All config lives in `src/config.py` as Pydantic `BaseSettings`:

- `LLMConfig` — provider selection (Azure/OpenAI/Anthropic/Google), API keys, models
- `ServerConfig` — host, port for FastAPI
- `MCPConfig` — port, skills directory for MCP server
- `ForgeConfig` — path to forge/ directory
- `ObservabilityConfig` — OTLP endpoint, log level

Settings are loaded from `.env` file. Access via `get_settings()` singleton.

---

## 8. Testing

- **363 tests** across 9 test files, all async (`pytest-asyncio`).
- Fixtures in `tests/conftest.py` — pre-built engine, agents, guardian, router.
- Test files map 1:1 to source domains (test_orchestrator, test_governance, etc.)
- CI: GitHub Actions, Python 3.11 + 3.12 matrix, ruff lint + format + pytest.

---

## 9. Common Tasks Quick Reference

| Task | Key files to modify |
|------|-------------------|
| Add a new agent | `forge/agents/<id>/agent.yaml`, `src/agents/<id>_agent.py`, `AgentType` enum in `router.py`, `_SPECIALISED_CLASSES` in `main.py` |
| Change token budgets | `forge/_context_window.yaml` or `forge/agents/<id>/agent.yaml` — recalculate sum! |
| Add an HTTP endpoint | `src/server.py` — add route, update module docstring |
| Add a HITL gate | Follow pattern in `src/governance/selector.py` or `src/orchestrator/plan_selector.py` |
| Add a skill | `forge/agents/<id>/skills/<name>.yaml` |
| Change routing | `src/orchestrator/router.py` — add keyword patterns to `AgentType` |
