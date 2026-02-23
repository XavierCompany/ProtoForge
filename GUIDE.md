# ProtoForge — Developer Guide

> **TL;DR for LLMs**: Deep-dive developer reference (2750+ lines / 19 sections).
> Read all sections for complete understanding. Use the section index in
> [ARCHITECTURE.md §10](ARCHITECTURE.md#10-llm-documentation-reading-order)
> to navigate quickly to specific topics.
>
> This is doc **8 of 9** in the reading order. Read
> [.github/copilot-instructions.md](.github/copilot-instructions.md)
> → [ARCHITECTURE.md](ARCHITECTURE.md) first for orientation.

A comprehensive guide covering architecture rationale, extending agent capabilities, and leveraging GitHub Copilot CLI for AI-powered code review workflows.

---

## Table of Contents

1. [Why This Architecture?](#why-this-architecture)
2. [Plan-First Design: The Reasoning](#plan-first-design-the-reasoning)
3. [Architecture Design & Flow](#architecture-design--flow)
4. [Context Window Management — Why & How](#context-window-management--why--how)
5. [Splitting Tasks: Agents, Skills & Sub-Agents](#splitting-tasks-agents-skills--sub-agents)
6. [Governance Guardian (Always-On Enforcement)](#governance-guardian-always-on-enforcement)
7. [The Forge Ecosystem](#the-forge-ecosystem)
8. [Agent Registry / Catalog](#agent-registry--catalog)
9. [Expanding Plan Agent Capabilities](#expanding-plan-agent-capabilities)
10. [Expanding Sub-Agent Capabilities](#expanding-sub-agent-capabilities)
11. [Adding a Brand-New Agent](#adding-a-brand-new-agent)
12. [Adding New Skills & Workflows](#adding-new-skills--workflows)
13. [Dynamic Contributions (CRUD)](#dynamic-contributions-crud)
14. [Sub-Plan Agent (Dual HITL Resource Planning)](#sub-plan-agent-dual-hitl-resource-planning)
15. [WorkIQ Integration (2-Phase Human-in-the-Loop)](#workiq-integration-2-phase-human-in-the-loop)
16. [Extending the Codebase with GitHub Copilot CLI](#extending-the-codebase-with-github-copilot-cli)
17. [Multi-Model Code Review Workflow](#multi-model-code-review-workflow-copilot-cli--claude-opus-46--codex-53)
18. [Architecture Decision Records](#architecture-decision-records)
19. [How to Add a Pre-Router Enrichment Source](#how-to-add-a-pre-router-enrichment-source)

---

## Why This Architecture?

### The Problem with Flat Multi-Agent Systems

Most multi-agent systems use a flat architecture where a router directly dispatches to a single specialist agent:

```
User → Router → Agent → Response    ← flat, no coordination
```

This breaks down when:
- A request requires **multiple agents** (e.g., "fix the crash in the auth module" needs log analysis + code research + remediation)
- There's **no strategic plan** — each agent works in isolation without shared context
- **Sequencing matters** — you need to analyze logs *before* writing a fix
- **Quality degrades** — no coordinator verifies the overall approach

### Why Plan-First Solves This

ProtoForge uses a **Plan-First** architecture — every request goes through the Plan Agent before any sub-agent executes:

```
User → Orchestrator → Plan Agent (ALWAYS first)
                         ↓
                    Sub-Agents (parallel fan-out)
                         ↓
                    Aggregated Response
```

**Benefits:**

| Benefit | How Plan-First Delivers It |
|---------|---------------------------|
| **Strategic consistency** | Plan Agent produces a step-by-step strategy before execution |
| **Multi-agent coordination** | Plan Agent identifies which sub-agents to invoke and why |
| **Shared context** | Plan output is stored in working memory — sub-agents can reference it |
| **Quality gate** | Plan serves as a top-level review before work begins |
| **Parallel execution** | Once planned, sub-agents run concurrently for speed |
| **Observability** | Every response shows the plan + individual agent outputs |

### Why Microsoft Agent Framework?

We chose **[Microsoft Agent Framework (Python)](https://learn.microsoft.com/en-us/agent-framework/overview/?pivots=programming-language-python)** over AutoGen, LangGraph, and CrewAI because:

- **Unified agent runtime** — first-class agents, activities, and channels in a single framework
- **Native multi-LLM** — switch between Azure OpenAI, OpenAI, Anthropic, Google without code changes
- **Enterprise-grade** — built by Microsoft, production-tested, strong Azure integration and identity management
- **Activity-based orchestration** — declarative pipelines with fan-out/fan-in, retries, and observability built in
- **MCP-compatible** — skills map naturally to MCP tools for cross-tool interop
- **Minimal abstraction tax** — thin wrapper over LLM calls with composable middleware, not a heavy framework
- **Semantic Kernel interop** — can leverage Semantic Kernel plugins and connectors as needed

---

## Plan-First Design: The Reasoning

### How `engine.py` Works

The engine provides **two entry points** — standard routing and WorkIQ-enriched routing:

#### Standard Flow — `process()`

After the P0-2 refactor, `process()` computes routing then **delegates** to `_process_after_routing()` — eliminating ~30 lines of duplicate pipeline code:

```python
async def process(self, user_message: str) -> str:
    # 1. Record user message & reset governance counters
    self._context.add_user_message(user_message)
    if self._governance:
        self._governance.reset_run()

    # 2. Route intent — keyword patterns identify target agent types
    routing = self._router.route_by_keywords(user_message)

    # 3. Delegate to shared pipeline (same path as enriched flow)
    return await self._process_after_routing(user_message, routing)
```

The shared `_process_after_routing()` handles the rest:

```python
async def _process_after_routing(self, user_message: str, routing) -> str:
    # 1. Low confidence? Try LLM routing
    if routing.confidence < 0.5:
        llm_routing = await self._route_with_llm(user_message)

    # 2. ALWAYS run Plan Agent first
    plan_result = await self._dispatch(AgentType.PLAN, user_message, routing)

    # 3. Store plan in working memory for sub-agents
    self._context.set_memory("plan_output", plan_result.content)
    self._context.set_memory("plan_artifacts", plan_result.artifacts)

    # 4. Sub-Plan pipeline — dual HITL gates
    sub_plan_result = await self._run_sub_plan_pipeline(user_message, plan_result)

    # 5. Resolve which sub-agents to invoke (excludes PLAN + SUB_PLAN)
    sub_agents = self._resolve_sub_agents(routing)

    # 6. Fan out task agents in parallel
    sub_results = await self._fan_out(sub_agents, user_message, routing)

    # 7. Aggregate Plan + Sub-Plan + task agent results
    return self._aggregate(plan_result, sub_results, sub_plan_result)
```

#### Enriched Flow — `process_with_enrichment()`

When `WorkIQClient` and `WorkIQSelector` are wired into the engine, the enriched pipeline adds a pre-routing layer:

```python
async def process_with_enrichment(self, user_message: str) -> str:
    # Phase 0 — query Work IQ for M365 context
    workiq_result = await self._workiq_client.ask(user_message)

    # Phase 1 — HITL: user selects relevant content sections
    content_req = self._workiq_selector.prepare(workiq_result, request_id)
    selected_text = await self._workiq_selector.wait_for_selection(request_id)

    # Phase 2 — extract routing keywords from selected content
    hints = self._router.extract_routing_keywords(selected_text)

    # Phase 2b — HITL: user accepts/rejects keyword hints
    hint_req = self._workiq_selector.prepare_routing_hints(hints, hint_id)
    accepted = await self._workiq_selector.wait_for_routing_hints(hint_id)

    # Phase 3 — enriched routing (message + accepted keyword boosts)
    routing = self._router.route_with_context(user_message, accepted)

    # Continue: Plan Agent → Sub-Agents → Aggregate
    return await self._process_after_routing(user_message, routing)
```

If WorkIQ is not configured or fails at any phase, the engine transparently falls back to the standard `process()` pipeline.

### Why Plan Agent Is Always First

The Plan Agent (`src/agents/plan_agent.py`) acts as a **strategic coordinator**:

1. **Analyzes scope** — understands what the user is really asking
2. **Decomposes** — breaks complex requests into ordered steps
3. **Routes** — recommends which sub-agents should execute (and why)
4. **Provides context** — downstream sub-agents can read the plan from working memory
5. **Sets success criteria** — defines what "done" looks like

This means even a simple request like *"check the logs"* gets a plan that identifies:
- What to look for in the logs
- Whether other agents might help (e.g., code_research for stack traces)
- What success looks like

---

## Architecture Design & Flow

### The Full Orchestration Pipeline

Every request in ProtoForge flows through a carefully designed pipeline with governance enforcement at every stage:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        HTTP Server (FastAPI)                            │
│  /chat  /chat/enriched  /mcp  /agents  /skills  /governance/*          │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │
         ┌──────────────────────▼──────────────────────┐
         │           WorkIQ Pre-Router (optional)       │
         │  Phase 0: workiq ask → Phase 1: HITL select  │
         │  Phase 2: keywords → Phase 2b: HITL accept   │
         └──────────────────────┬──────────────────────┘
                                │  routing hints
         ┌──────────────────────▼──────────────────────┐
         │  🛡️ Governance Guardian (always-on)          │
         │  ┌──────────────────────────────────────┐   │
         │  │ Context Window: 128K cap, 110K warn  │   │
         │  │ Fan-out cap: max 3 specialists        │   │
         │  │ Skill Cap: max 4 per agent            │   │
         │  │ Architecture: agents=tasks             │   │
         │  └──────────────────────────────────────┘   │
         └──────────────────────┬──────────────────────┘
                                │
         ┌──────────────────────▼──────────────────────┐
         │           Intent Router                      │
         │  Keyword matching → LLM fallback if < 0.5   │
         │  + WorkIQ enrichment hints (if available)    │
         └──────────────────────┬──────────────────────┘
                                │  ALWAYS first
         ┌──────────────────────▼──────────────────────┐
         │           Plan Agent (Coordinator)           │
         │  Analyzes → Decomposes → Routes → Criteria  │
         │  🛡️ Pre-dispatch governance check            │
         └──────────────────────┬──────────────────────┘
                                │  HITL: user accepts plan
         ┌──────────────────────▼──────────────────────┐
         │           Sub-Plan Agent (Resources)         │
         │  Plans minimum-viable prerequisites          │
         │  🛡️ Pre-dispatch governance check            │
         └──────────────────────┬──────────────────────┘
                                │  HITL: user accepts resources
         ┌──────────┬──────────┼──────────┐
         ▼          ▼          ▼          ▼
    ┌─────────┐ ┌─────────┐ ┌─────────┐
    │  Log    │ │  Code   │ │Security │ (max 3 specialists)
    │Analysis │ │Research │ │Sentinel │
    │ 🛡️pre  │ │ 🛡️pre  │ │ 🛡️pre  │
    │ 🛡️post │ │ 🛡️post │ │ 🛡️post │
    └────┬────┘ └────┬────┘ └────┬────┘
         └──────────┬┘───────────┘
                    ▼
         ┌──────────────────────────────────────────┐
         │           Aggregation Layer               │
         │  Plan + Sub-Plan + task results combined  │
         └──────────────────────────────────────────┘
```

### Key Design Principles

1. **Plan-first, always** — every request gets a strategic plan before any agent executes
2. **Governance at every stage** — token usage checked pre- and post-dispatch for every agent
3. **Hard cap is enforced** — if cumulative tokens reach 128K, execution aborts with `ContextWindowExceededError` (fail-closed); the task must be decomposed
4. **Fan-out cap** — max 3 specialists per orchestration run (enforced in `_resolve_sub_agents()`), keeping worst-case at 124K tokens
5. **Per-agent budget enforcement** — every dispatch allocates a budget via `ContextBudgetManager`, inputs are truncated before reaching the agent
6. **HITL at every decision boundary** — humans approve plans, resources, and governance alerts
7. **Fail-open timeouts** — all HITL gates auto-resolve after 120s to prevent pipeline stalls
8. **Parallel fan-out** — independent task agents run concurrently after planning is complete
9. **Context isolation** — sub-agents run in separate context windows to prevent overflow

### Working Memory Flow

ProtoForge uses a shared `ConversationContext` that carries state across the pipeline:

```
Plan Agent writes:
  ├── plan_output        → Strategic plan text
  ├── plan_artifacts     → {recommended_sub_agents: [...]}
  └── plan_keywords      → Routing keywords

Sub-Plan Agent writes:
  ├── sub_plan_output    → Resource deployment plan
  ├── sub_plan_artifacts → {resource_items: [...]}
  └── resource_brief     → Human brief or default minimum-viable

WorkIQ enrichment writes:
  ├── workiq_selected_content  → User-selected M365 text
  └── workiq_accepted_hints    → [{agent, keyword}, ...]

Governance writes:
  ├── governance_alerts        → Active alerts (context, skill cap)
  └── governance_report        → Full status snapshot

Task agents read:
  ├── plan_output        → Reference the strategic plan
  ├── resource_brief     → Know what resources are available
  └── workiq_*           → M365 context (if enriched flow)
```

---

## Context Window Management — Why & How

### Why Context Windows Matter

LLM context windows are the **single most critical resource** in a multi-agent system:

1. **They're finite** — with a 128K token hard cap per orchestration run, Plan + Sub-Plan + 3 specialists must share a single budget
2. **They're expensive** — token cost scales linearly; waste 50K tokens on irrelevant context and you're paying for noise
3. **Quality degrades** — LLMs perform worse as context fills up ("lost in the middle" phenomenon); keeping context focused improves output quality
4. **They're shared** — in a multi-agent orchestration, all agents draw from the same token budget for a single run

### The Two-Layer Token Budget System

ProtoForge manages context at two levels:

#### Layer 1 — Per-Agent Budgets (`ContextBudgetManager`)

Each agent has its own input/output token budget defined in its `agent.yaml` manifest:

```yaml
# forge/agents/log_analysis/agent.yaml
context_budget:
  max_input_tokens: 15000    # max tokens for input to this agent
  max_output_tokens: 7000    # max tokens for output from this agent
  strategy: sliding_window   # how to handle overflow
```

The `ContextBudgetManager` (`src/forge/context_budget.py`) enforces these per-agent limits **at dispatch time** inside `engine.py`:

```python
# Called automatically in engine._dispatch() for every agent execution
budget = manager.allocate("log_analysis", "specialist", override=manifest.context_budget)

# Check if content fits the allocated budget
if not manager.fits_budget("log_analysis", content, direction="input"):
    content = manager.truncate("log_analysis", content, direction="input")

# Record actual usage after execution
manager.record_usage("log_analysis", input_tokens, direction="input")
manager.record_usage("log_analysis", output_tokens, direction="output")
```

**Optimized Per-Agent Budget Table** (designed to guarantee ≤ 128K worst case):

| Agent | Type | Input | Output | Strategy | Envelope |
|-------|------|-------|--------|----------|----------|
| Plan | coordinator | 24,000 | 8,000 | priority | 32K |
| Sub-Plan | specialist | 14,000 | 6,000 | priority | 20K |
| Knowledge Base | specialist | 17,000 | 8,000 | summarize | 25K (heavy) |
| Code Research | specialist | 17,000 | 8,000 | sliding_window | 25K (heavy) |
| Log Analysis | specialist | 15,000 | 7,000 | sliding_window | 22K |
| Data Analysis | specialist | 15,000 | 7,000 | sliding_window | 22K |
| Remediation | specialist | 15,000 | 7,000 | priority | 22K |
| Security Sentinel | specialist | 15,000 | 7,000 | priority | 22K |
| GitHub Tracker | specialist | 15,000 | 7,000 | priority | 22K |
| WorkIQ | specialist | 12,000 | 6,000 | priority | 18K (light) |

**Worst case** (Plan + Sub-Plan + 2 heavy + 1 medium): 32 + 20 + 25 + 25 + 22 = **124K** (4K headroom)
**Typical** (Plan + Sub-Plan + 2 medium): 32 + 20 + 22 + 22 = **96K** (32K headroom)

**Three truncation strategies** handle overflow differently:

| Strategy | How It Works | Best For |
|----------|-------------|----------|
| `priority` | Keeps content by priority order (errors > stack traces > code > metadata). Drops lowest-priority content first | Plan Agent, general use |
| `sliding_window` | Keeps the most recent N tokens, oldest content drops off | Log Analysis (recent entries matter most) |
| `summarize` | LLM-compresses content before passing to agent; falls back to `priority` if LLM unavailable | Knowledge Base, long documents |

#### Layer 2 — Global Budget (`GovernanceGuardian`)

The `GovernanceGuardian` tracks **cumulative** token usage across all agents in a single orchestration run:

```
Global budget: 128,000 tokens
  ├── Plan Agent:      up to 32,000
  ├── Sub-Plan Agent:  up to 20,000
  ├── 3 Specialists:   up to 25,000 each (max 75,000)
  └── Headroom:        ~4,000
```

The guardian enforces two thresholds with **real enforcement** (not advisory):

| Threshold | Tokens | What Happens |
|-----------|--------|-------------|
| **Warning** | 110,000 | HITL triggered — `ContextWindowReview` created for human review. Guardian presents per-agent usage breakdown and suggests decomposition |
| **Hard cap** | 128,000 | `ContextWindowExceededError` **raised** — dispatch aborts immediately with confidence=0.0. The task MUST be decomposed. This is fail-closed: no agent executes beyond this point |

#### Layer 3 — Fan-Out Cap

The `_resolve_sub_agents()` method enforces a maximum of **3 specialists** per orchestration run (configured via `max_parallel_agents` in `_context_window.yaml`). Excess candidates are dropped in priority order with a warning log.

### How Governance Checks Work at Runtime

The `OrchestratorEngine._dispatch()` method performs budget enforcement at every agent execution:

```python
# In engine.py — _dispatch() method (simplified)

async def _dispatch(self, agent_id, message, routing):
    # ── PER-AGENT BUDGET: allocate & truncate ──
    effective_message = message
    if self._budget_manager:
        override = manifest.context_budget if manifest else None
        self._budget_manager.allocate(agent_id, agent_type, override=override)

        payload = message + agent.system_prompt
        if not self._budget_manager.fits_budget(agent_id, payload, direction="input"):
            effective_message = self._budget_manager.truncate(agent_id, message, direction="input")

    # ── PRE-DISPATCH: governance hard cap check ──
    estimated_tokens = budget_manager.count_tokens(effective_message + system_prompt)
    try:
        alert = guardian.check_context_window(agent_id, estimated_tokens)
    except ContextWindowExceededError:
        # Hard cap breached → abort immediately (fail-closed)
        return AgentResult(agent_id=agent_id, content="Hard cap exceeded", confidence=0.0)

    if alert and alert.level == "warning":
        await self._handle_governance_alert(alert)  # HITL review

    # ── EXECUTE ──
    result = await agent.execute(effective_message, context)

    # ── POST-DISPATCH: record usage ──
    budget_manager.record_usage(agent_id, input_tokens, direction="input")
    budget_manager.record_usage(agent_id, output_tokens, direction="output")
    guardian.record_agent_usage(agent_id, total_tokens)
```

### The HITL Decomposition Flow

When the warning threshold is crossed, here's exactly what happens:

```
1. GovernanceGuardian.check_context_window() → GovernanceAlert(level=WARNING)
2. Engine calls _handle_governance_alert()
3. GovernanceSelector.prepare_context_review() → ContextWindowReview
   └── Staged for human at GET /governance/context-reviews
       Includes:
       - Current cumulative tokens (e.g., 121,000)
       - Per-agent breakdown (Plan: 8K, Log: 15K, Code: 20K, ...)
       - Suggestion: "Decompose remaining work into log_analysis_overflow sub-agent"

4. Human reviews at POST /governance/context-reviews/{id}/resolve
   Option A: accepted=true  → Task is decomposed, sub-agent spawned with fresh 128K window
   Option B: accepted=false → Execution continues at operator's risk

5. If timeout (120s) → auto-resolve as "accept" (fail-open)
```

### Practical Example: A 5-Agent Orchestration

Consider a complex request: *"Investigate the auth outage, fix it, and audit for security gaps"*

With the enforced fan-out cap (max 3 specialists) and optimized budgets:

```
Plan Agent:
  Input: ~8K (system prompt + user message + routing context)
  Output: ~4K (strategic plan + agent recommendations)
  Cumulative: 12K ✅

Sub-Plan Agent:
  Input: ~5K (plan output + resource planning prompt)
  Output: ~3K (minimum viable resources)
  Cumulative: 20K ✅

Log Analysis (specialist 1/3):
  Input: ~15K (system prompt + log content — sliding_window truncated to budget)
  Output: ~7K (error analysis + patterns)
  Cumulative: 42K ✅

Code Research (specialist 2/3):
  Input: ~17K (codebase snippets + plan context — sliding_window truncated)
  Output: ~8K (root cause analysis)
  Cumulative: 67K ✅

Security Sentinel (specialist 3/3):
  Input: ~15K (CVE databases + code audit — priority truncated to budget)
  Output: ~7K (vulnerability report)
  Cumulative: 89K ✅

  → Aggregation combines all results
  → Total: 89K of 128K budget used (69.5% utilisation)
  → Remediation was dropped by fan-out cap — route as follow-up request
```

**Note:** If the router requested 4+ specialists, `_resolve_sub_agents()` would truncate to the top 3 by priority order and log the dropped agents.

### Configuration Reference

All context window settings live in `forge/_context_window.yaml`:

```yaml
version: "1.2.0"

global:
  max_total_tokens: 128000
  reserve_for_plan: 32000
  reserve_for_aggregation: 8000

scaling:
  max_parallel_agents: 3   # fan-out cap — enforced in _resolve_sub_agents()

governance:
  context_window:
    warning_threshold: 110000     # HITL at this level
    hard_cap: 128000              # execution aborts (ContextWindowExceededError)
    enforce_hard_cap: true        # true = fail-closed, false = advisory only
    check_before_dispatch: true
    check_after_dispatch: true
  budget_enforcement:
    allocate_on_dispatch: true    # allocate per-agent budget before execute()
    truncate_on_dispatch: true    # auto-truncate inputs that exceed budget
  skill_cap:
    max_skills_per_agent: 4
    allow_override: true
  hitl:
    timeout_seconds: 120
    auto_resolve_action: accept

defaults:
  specialist:
    max_input_tokens: 15000
    max_output_tokens: 7000
    strategy: priority
  coordinator:
    max_input_tokens: 24000
    max_output_tokens: 8000
    strategy: priority
```

#### Budget Enforcement Flags

| Flag | Default | Effect |
|------|---------|--------|
| `enforce_hard_cap` | `true` | When `true`, `check_context_window()` raises `ContextWindowExceededError` on hard cap breach (fail-closed). When `false`, returns a CRITICAL alert but allows execution to continue (advisory) |
| `allocate_on_dispatch` | `true` | When `true`, `_dispatch()` calls `budget_manager.allocate()` before every agent execution |
| `truncate_on_dispatch` | `true` | When `true`, `_dispatch()` calls `budget_manager.truncate()` when input exceeds the per-agent budget |
| `max_parallel_agents` | `3` | Maximum number of specialist agents in a single fan-out. Excess candidates are dropped by priority order |

---

## Splitting Tasks: Agents, Skills & Sub-Agents

Understanding the **separation of concerns** between agents, skills, and sub-agents is the key to building a maintainable multi-agent system. The governance system enforces these boundaries automatically.

### The Three Component Types

#### Agents — Task Handlers

An **agent** handles a complete task. It has its own system prompt, context budget, and skills. It receives a goal and returns a structured result.

```
Agent = Task
  ├── Has its own context window budget
  ├── Has ≤ 4 skills (governance enforced)
  ├── Can read working memory (plan context, prior results)
  └── Returns AgentResult (content, confidence, artifacts)
```

**Examples:** Log Analysis Agent (diagnose log errors), Security Sentinel (audit for vulnerabilities), Remediation Agent (generate fixes)

#### Skills — Reusable Capabilities

A **skill** is a single, stateless capability that an agent can invoke — like a tool. Skills are defined in YAML and exposed as MCP tools.

```
Skill = Capability
  ├── Stateless — no context window of its own
  ├── Defined in YAML (name, description, parameters)
  ├── Belongs to one agent (or shared/)
  └── Exposed as MCP tool automatically
```

**Examples:** `analyze_logs` (parse log files), `search_code` (find code patterns), `scan_vulnerabilities` (CVE lookup)

#### Sub-Agents — Context Isolation

A **sub-agent** handles context-heavy work in its own **fresh context window**. This prevents the parent agent from overflowing its context budget.

```
Sub-Agent = Context Isolation
  ├── Runs in a FRESH context window (128K available)
  ├── Inherits governance rules (128K cap, 4-skill limit)
  ├── Created when parent would overflow
  └── Results folded back into parent's working memory
```

**Examples:** A `log_analysis_overflow` sub-agent for processing massive log files, a `code_research_deep` sub-agent for multi-file codebase analysis

### Decision Matrix: What to Use When

| Scenario | Component | Rationale |
|----------|-----------|-----------|
| Need to parse log files | **Skill** on Log Analysis Agent | Stateless tool — no context overhead |
| Need to diagnose an outage from logs | **Log Analysis Agent** | Complete task with its own goal and context |
| Log files are 100K+ tokens | **Sub-agent** under Log Analysis | Context isolation — fresh window prevents overflow |
| Agent needs 6 skills | **Split: agent (4) + sub-agent (2)** | Governance enforces 4-skill cap |
| Orchestration hits 120K tokens | **Sub-agent** for remaining work | Governance HITL triggers decomposition |
| Reusable API call (GitHub, Jira) | **Skill** | Stateless, reusable across agents |
| Full security audit of codebase | **Security Sentinel Agent** | Complete task with dedicated context |
| Security audit needs to process 200 files | **Sub-agent** per file batch | Context isolation per batch |

### How Sub-Agent Spawning Works

When the governance system triggers a context decomposition:

```
1. GovernanceGuardian detects: cumulative tokens > 120K

2. HITL Review created:
   "Agent 'knowledge_base' would push context to 125K.
    Suggest creating 'knowledge_base_overflow' sub-agent."

3. Human accepts → engine spawns sub-agent:
   - Fresh 128K context window
   - Inherits parent's system prompt + plan context (compressed)
   - Executes remaining work
   - Returns result to parent's working memory

4. Aggregation layer combines:
   - Plan Agent output
   - Direct task agent outputs
   - Sub-agent outputs
   → Final response to user
```

### The 4-Skill Rule

Each agent is limited to **4 skills** by governance. This forces clean separation:

```
❌ Overloaded Agent (6 skills):
  security_sentinel
  ├── scan_vulnerabilities
  ├── check_dependencies
  ├── audit_permissions
  ├── review_network_config    ← skill 4
  ├── analyze_encryption       ← skill 5 — GOVERNANCE VIOLATION
  └── check_compliance         ← skill 6 — GOVERNANCE VIOLATION

✅ Properly Split (2 agents, 4 + 2 skills):
  security_sentinel (primary)
  ├── scan_vulnerabilities
  ├── check_dependencies
  ├── audit_permissions
  └── review_network_config

  security_sentinel_overflow (sub-agent)
  ├── analyze_encryption
  └── check_compliance
```

The `GovernanceGuardian` generates this split suggestion automatically at manifest load time, surfaced for human review via the HITL gate.

---

## Governance Guardian (Always-On Enforcement)

The **Governance Guardian** (`src/governance/guardian.py`) is an always-on enforcement system that runs at every stage of the orchestration pipeline. It cannot be disabled by individual agents and enforces three pillars: context window management, skill cap limits, and architectural principles.

### Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                   Governance Guardian                                │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  │
│  │ Context Window    │  │ Skill Cap        │  │ Architecture     │  │
│  │ ─────────────── │  │ ──────────────── │  │ ──────────────── │  │
│  │ 128K hard cap     │  │ 4 skills max     │  │ Agents = tasks   │  │
│  │ 120K warning      │  │ HITL on overflow │  │ Skills = tools   │  │
│  │ Pre/post dispatch │  │ Split suggestion │  │ Sub-agents =     │  │
│  │ HITL decomposes   │  │ at manifest load │  │ context isolation │  │
│  └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘  │
│           │                     │                      │            │
│  ┌────────▼─────────────────────▼──────────────────────▼─────────┐  │
│  │              GovernanceSelector (HITL Gates)                    │  │
│  │  ContextWindowReview    SkillCapReview    AgentLifecycleReview │  │
│  │  prepare → expose → wait → resolve                             │  │
│  │  Context/Skill: Timeout 120s → fail-open (accept suggestion)   │  │
│  │  Lifecycle:     Timeout 120s → fail-CLOSED (reject action)     │  │
│  └────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

### Components

| Component | File | Purpose |
|-----------|------|---------|
| `GovernanceGuardian` | `src/governance/guardian.py` | Core enforcement: `check_context_window()`, `validate_skill_cap()`, `audit_manifest()`, `governance_report()` |
| `GovernanceSelector` | `src/governance/selector.py` | HITL gates: `ContextWindowReview`, `SkillCapReview`, and `AgentLifecycleReview` with prepare → expose → wait → resolve pattern. Context/skill reviews fail-open on timeout; lifecycle reviews **fail-CLOSED** (reject action on timeout). |
| Governance rules | `forge/shared/instructions/governance_rules.md` | Human-readable rules injected into every agent's system prompt |
| Governance config | `forge/_context_window.yaml` (governance section) | Thresholds, timeouts, caps — all configurable |

### GovernanceGuardian API

```python
from src.governance.guardian import GovernanceGuardian, ContextWindowExceededError

# Initialised at bootstrap with context_window.yaml config
guardian = GovernanceGuardian(config=context_config, budget_manager=budget_manager)

# ── Context Window ──
try:
    alert = guardian.check_context_window("log_analysis", estimated_tokens=15000)
    # Returns None (healthy) or GovernanceAlert(WARNING)
except ContextWindowExceededError as exc:
    # Hard cap breached and enforce_hard_cap=true → abort
    print(exc.alert)  # GovernanceAlert(CRITICAL)

guardian.record_agent_usage("log_analysis", tokens_used=14500)

# ── Skill Cap ──
alert = guardian.validate_skill_cap(manifest)
# Returns None or GovernanceAlert with SkillSplitSuggestion

# ── Architectural Audit ──
alerts = guardian.audit_manifest(manifest)
# Returns list of GovernanceAlert (skill cap + architecture hints)

# ── Status ──
report = guardian.governance_report()
# {cumulative_tokens, hard_cap, utilisation_pct, agent_usage, alerts, violations}

guardian.reset_run()  # reset at start of new orchestration
```

### GovernanceSelector API (HITL)

```python
from src.governance.selector import GovernanceSelector

selector = GovernanceSelector(timeout=120.0)

# ── Context Window Review ──
review = selector.prepare_context_review(request_id, alert, decomposition)
pending = selector.pending_context_reviews()  # for REST API
selector.resolve_context_review(request_id, accepted=True, user_note="Split KB work")
result = await selector.wait_for_context_review(request_id)

# ── Skill Cap Review ──
review = selector.prepare_skill_review(request_id, alert, split_suggestion)
pending = selector.pending_skill_reviews()
selector.resolve_skill_review(request_id, accepted=True)
# Or customise the split:
selector.resolve_skill_review(
    request_id, accepted=True,
    custom_keep=["scan_vulns", "check_deps", "audit_perms", "review_net"],
    custom_overflow=["analyze_encrypt", "check_compliance"],
)
# Or override (acknowledge violation, proceed with > 4 skills):
selector.resolve_skill_review(request_id, accepted=False, overridden=True)
```

### REST API: Governance Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/governance/status` | Full governance report: cumulative tokens, utilisation %, per-agent usage, alert counts, violations |
| GET | `/governance/alerts` | All alerts (resolved + unresolved) |
| GET | `/governance/alerts/unresolved` | Only unresolved alerts |
| POST | `/governance/alerts/{id}/resolve` | Resolve an alert (`{resolution: "accepted"}`) |
| GET | `/governance/context-reviews` | Pending context window HITL reviews (includes token breakdown + decomposition suggestion) |
| POST | `/governance/context-reviews/{id}/resolve` | Accept/reject context decomposition (`{accepted: true, user_note: "..."}`) |
| GET | `/governance/skill-reviews` | Pending skill cap HITL reviews (includes split suggestion) |
| POST | `/governance/skill-reviews/{id}/resolve` | Accept/reject/customise skill split |

### Example: Governance Status Response

```json
GET /governance/status

{
  "cumulative_tokens": 95000,
  "hard_cap": 128000,
  "warning_threshold": 120000,
  "utilisation_pct": 74.2,
  "agent_usage": {
    "plan": 12000,
    "sub_plan": 8000,
    "log_analysis": 23000,
    "code_research": 28000,
    "security_sentinel": 24000
  },
  "max_skills_per_agent": 4,
  "total_alerts": 1,
  "unresolved_alerts": 0,
  "skill_violations": []
}
```

---

## The Forge Ecosystem

The `forge/` directory is the **declarative backbone** of ProtoForge. Instead of hard-coding agent configurations into Python, every agent manifest, prompt, skill, workflow, instruction, and context budget is defined in YAML/Markdown and auto-discovered at startup.

### Why a Separate `forge/` Directory?

| Concern | Before | After (`forge/`) |
|---------|--------|-------------------|
| Agent configuration | Scattered across Python classes | Centralized YAML manifests |
| Prompts & instructions | Inline strings in code | Markdown files, version-controlled |
| Skills & workflows | Flat `skills/` and `workflows/` dirs | Co-located with their owning agent |
| Context budgets | Not managed | Per-agent YAML config with 3 strategies |
| Adding agents | Requires Python code changes | Drop a folder into `forge/agents/` or `forge/contrib/` |
| Audit trail | None | Built-in audit log for all contribution changes |

### Directory Layout

```
forge/
├── _registry.yaml              # Master registry — all agents & shared resources
├── _context_window.yaml        # Global + per-agent token budget configuration
├── plan/                       # Plan Agent (coordinator)
│   ├── agent.yaml              #   Manifest (id, subagents, context_budget, etc.)
│   ├── prompts/                #   system.md, strategy.md, decomposition.md, routing.md
│   ├── skills/                 #   plan_task.yaml, identify_agents.yaml, build_strategy.yaml
│   ├── instructions/           #   routing_rules.md, coordination.md
│   └── workflows/              #   plan_and_execute.yaml
├── agents/                     # 8 specialist agents (each has same structure)
│   ├── sub_plan/               #   Sub-Plan Agent (resource planner + dual HITL)
│   ├── log_analysis/           #   agent.yaml + prompts/ + skills/ + instructions/
│   ├── code_research/
│   ├── remediation/
│   ├── knowledge_base/
│   ├── data_analysis/
│   ├── security_sentinel/
│   └── workiq/                 #   WorkIQ agent (M365 context + 2-phase HITL)
├── shared/                     # Cross-agent resources
│   ├── prompts/                #   error_handling.md, output_format.md
│   ├── instructions/           #   quality_standards.md, security_baseline.md
│   └── workflows/              #   code_review.yaml, incident_response.yaml
└── contrib/                    # Dynamic contributions (CRUD via ContributionManager)
    ├── audit_log.yaml          #   Timestamped audit trail of all changes
    ├── agents/                 #   Drop-in contributed agents
    ├── skills/                 #   Drop-in contributed skills
    └── workflows/              #   Drop-in contributed workflows
```

### How `ForgeLoader` Works

At bootstrap, `src/forge/loader.py` walks the `forge/` tree and builds a `ForgeRegistry`:

```python
# src/main.py — step 0
forge_loader = ForgeLoader(settings.forge.forge_dir)
forge_registry = forge_loader.load()
# forge_registry now contains:
#   .coordinator   → AgentManifest for plan_agent
#   .agents        → dict[str, AgentManifest] for 7 specialists
#   .skills        → list of all skill dicts (from all agents + shared + contrib)
#   .workflows     → list of all workflow dicts
#   .shared_prompts, .shared_instructions
#   .context_config → parsed _context_window.yaml
```

The loader:
1. Parses `_context_window.yaml` into the context config
2. Loads `plan/agent.yaml` → `AgentManifest` (coordinator)
3. Walks `agents/*/agent.yaml` → specialist manifests
4. **Resolves prompts/instructions** — reads `.md` files referenced in each manifest into memory
5. **Collects skills** — gathers all `skills/*.yaml` from every agent directory
6. **Collects workflows** — gathers all `workflows/*.yaml`
7. Loads `shared/` prompts, instructions, and workflows
8. Discovers `contrib/` additions (agents, skills, workflows)
9. **Populates the Agent Catalog** — `catalog.populate_from_skills(forge_registry.skills)` seeds the skill catalog from loaded manifests

### Agent Manifests in Detail

Every `agent.yaml` follows the same schema:

```yaml
id: log_analysis_agent           # Unique identifier
name: Log Analysis Agent         # Human-readable name
type: specialist                 # coordinator | specialist
description: >                  # What this agent does
  Expert in parsing, analyzing, and diagnosing application logs.
version: "1.0.0"

context_budget:                  # Token budget (overrides global defaults)
  max_input_tokens: 16000
  max_output_tokens: 8000
  strategy: sliding_window       # priority | sliding_window | summarize
  priority_order: [system_prompt, current_message, log_content, recent_history]

subagents: []                    # Only coordinators list sub-agents
prompts:
  system: system.md              # Resolved from prompts/system.md
skills:
  - analyze_logs.yaml            # Loaded from skills/analyze_logs.yaml
instructions:
  - log_formats.md               # Loaded from instructions/log_formats.md
tags: [logs, errors, debugging]
```

### Context Window Management

The `ContextBudgetManager` (`src/forge/context_budget.py`) enforces token budgets:

```python
from src.forge.context_budget import ContextBudgetManager

manager = ContextBudgetManager(forge_registry.context_config)

# Allocate a budget for an agent (uses agent.yaml context_budget as override)
budget = manager.allocate("log_analysis_agent", "specialist", override=manifest.context_budget)
# → TokenBudget(agent_id='log_analysis_agent', max_input=6000, max_output=3000, strategy='sliding_window')

# Count tokens in content
tokens = manager.count_tokens(long_text)  # uses tiktoken or ~4 chars/token

# Check if content fits
if manager.fits_budget("log_analysis_agent", long_text, direction="input"):
    pass  # content fits within 6000 input tokens

# Truncate if needed
truncated = manager.truncate("log_analysis_agent", long_text, direction="input")

# Track usage
manager.record_usage("log_analysis_agent", input_tokens=1200, output_tokens=800)

# Get a report of all agent usage
report = manager.usage_report()
```

**Three truncation strategies:**

| Strategy | Behavior | Best For |
|----------|----------|----------|
| `priority` | Keeps highest-priority content (error_messages > stack_traces > user_query > ...) | Plan Agent, general use |
| `sliding_window` | Keeps the most recent N tokens with configurable overlap | Log Analysis (recent entries matter most) |
| `summarize` | LLM-compresses content before passing to agent (falls back to priority) | Knowledge Base, long documents |

### Shared Resources

Files in `forge/shared/` are available to **all agents**:

- **`prompts/error_handling.md`** — Standard error classification (Recoverable / Degraded / Fatal), retry policies, escalation paths
- **`prompts/output_format.md`** — Standard response envelope (`agent_id`, `status`, `timestamp`, `tokens_used`, `payload`), formatting rules
- **`instructions/quality_standards.md`** — Response quality, code quality, security baseline, performance requirements
- **`instructions/security_baseline.md`** — Data handling, input validation, output safety, audit requirements
- **`workflows/code_review.yaml`** — 4-step review: security_scan → code_analysis → doc_check → review_summary
- **`workflows/incident_response.yaml`** — 5-step response: analyze_logs → research_code + security_check → diagnose → generate_fix

---

## Agent Registry / Catalog

The **Agent Catalog** (`src/registry/catalog.py`) is the central registry for managing sub-agents, skills, and their runtime state. It provides agent registration, discovery, health tracking, and persistence.

### Architecture

```
┌─────────────────────────────────────────────────┐
│              Agent Catalog                       │
│  ┌───────────────────┐  ┌────────────────────┐  │
│  │ Agent Registrations│  │  Skill Catalog     │  │
│  │  • agent_type      │  │  • skill_name      │  │
│  │  • name, desc      │  │  • agent_type      │  │
│  │  • version         │  │  • version, tags   │  │
│  │  • status          │  │  • installed flag   │  │
│  │  • skills, tags    │  │  • source           │  │
│  │  • usage_count     │  └────────────────────┘  │
│  │  • avg_latency_ms  │                          │
│  │  • error_rate      │  ┌────────────────────┐  │
│  └───────────────────┘  │  Persistence        │  │
│                          │  catalog.json       │  │
│                          └────────────────────┘  │
└─────────────────────────────────────────────────┘
```

### Managing Sub-Agents

#### Register an Agent

```python
from src.registry.catalog import AgentCatalog, AgentRegistration

catalog = AgentCatalog(storage_path=Path(".forge_catalog"))

catalog.register_agent(AgentRegistration(
    agent_type="log_analysis",
    name="Log Analysis Agent",
    description="Expert in parsing and diagnosing application logs",
    version="1.0.0",
    skills=["analyze_logs"],
    tags=["logs", "errors", "debugging"],
))
```

#### List, Filter, and Search Agents

```python
# List all active agents
for agent in catalog.list_agents(status="active"):
    print(f"{agent.name} — {agent.description}")
    print(f"  Skills: {agent.skills}")
    print(f"  Usage: {agent.usage_count} calls, {agent.avg_latency_ms:.0f}ms avg")

# Filter by tag
security_agents = catalog.list_agents(tag="security")
log_agents = catalog.list_agents(tag="logs")

# Unregister
catalog.unregister_agent("my_old_agent")
```

#### Track Agent Health & Metrics

The catalog tracks rolling averages for latency and error rate:

```python
# Record a successful call
catalog.update_agent_metrics("log_analysis", latency_ms=145.2, is_error=False)

# Record a failed call
catalog.update_agent_metrics("log_analysis", latency_ms=2000.0, is_error=True)

# Get catalog summary
status = catalog.get_status()
# → {"total_agents": 8, "active_agents": 8, "total_skills": 12, "installed_skills": 12}
```

#### Agent Status Management

Each agent registration tracks a `status` field (`active`, `disabled`, `degraded`) that the engine can use for routing decisions:

```python
agent = catalog.get_agent("security_sentinel")
if agent and agent.status == "active":
    # Route to this agent
    pass
```

### Skill Catalog

The skill catalog tracks installable skill packages and integrates with `ForgeLoader` for auto-population:

```python
from src.registry.catalog import CatalogEntry

# Add a skill to the catalog
catalog.add_to_catalog(CatalogEntry(
    skill_name="analyze_dependencies",
    description="Analyze project dependencies for vulnerabilities",
    agent_type="security_sentinel",
    version="1.0.0",
    tags=["security", "dependencies"],
))

# Install / uninstall
catalog.install_skill("analyze_dependencies")
catalog.uninstall_skill("analyze_dependencies")

# Search the skill catalog
results = catalog.search_catalog(query="security", installed_only=True)
results = catalog.search_catalog(agent_type="log_analysis")

# Bulk-populate from ForgeLoader
catalog.populate_from_skills(forge_registry.skills)
```

### Persistence

The catalog auto-persists to `catalog.json` whenever agents or skills are modified, and loads from disk at startup:

```python
# Persists to: <storage_path>/catalog.json
catalog = AgentCatalog(storage_path=Path(".forge_catalog"))

# Data format:
{
  "agents": {
    "log_analysis": {
      "agent_type": "log_analysis",
      "name": "Log Analysis Agent",
      "status": "active",
      "usage_count": 42,
      "avg_latency_ms": 156.3,
      "error_rate": 0.02,
      ...
    }
  },
  "catalog": {
    "analyze_logs": {
      "skill_name": "analyze_logs",
      "installed": true,
      "source": "local",
      ...
    }
  }
}
```

### REST API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/agents` | List all registered agents with capabilities and metrics |
| GET | `/skills` | List available skills (installed and catalog) |
| GET | `/health` | System status including catalog stats |

---

## Expanding Plan Agent Capabilities

### 1. Wire Up Real LLM Calls

The Plan Agent currently uses a structured placeholder. To wire it to a real LLM:

```python
# src/agents/plan_agent.py — inside execute()

from microsoft.agents.core import AgentRuntime
from microsoft.agents.ai import ChatCompletionService

async def execute(self, message, context, params=None):
    runtime = AgentRuntime()
    runtime.add_service(ChatCompletionService(
        model="claude-opus-4.6",  # or gpt-5.3-codex, codex-5.3
        endpoint=settings.llm.azure_endpoint,  # optional for non-Azure
    ))

    messages = self._build_messages(message, context)

    # Use Microsoft Agent Framework to get the LLM response
    result = await runtime.invoke_prompt(
        prompt=messages[-1]["content"],
        system_message=self._system_prompt,
    )

    # Parse the plan from the LLM output
    plan_text = str(result)
    recommended = self._identify_sub_agents(message, params)

    return AgentResult(
        agent_id=self.agent_id,
        content=plan_text,
        confidence=0.9,
        artifacts={"recommended_sub_agents": recommended},
    )
```

### 2. Add Plan Memory & Learning

Make the Plan Agent learn from past plans:

```python
# In plan_agent.py — add to execute()

# Check if similar plans exist in memory
past_plans = context.get_memory("past_plans") or []
similar = [p for p in past_plans if self._similarity(p["query"], message) > 0.8]

if similar:
    # Reference prior successful plans
    plan_prompt += f"\n\nPrevious similar plan:\n{similar[0]['plan']}"

# After generating the plan, store it
past_plans.append({"query": message, "plan": plan_response})
context.set_memory("past_plans", past_plans[-20:])  # keep last 20
```

### 3. Add Plan Validation & Self-Critique

Add a self-critique step so the Plan Agent reviews its own output:

```python
# After generating the initial plan
critique_prompt = (
    f"Review this plan for completeness and risks:\n{plan_response}\n\n"
    f"Is anything missing? Are the steps in the right order? "
    f"Are the right sub-agents selected?"
)
critique = await runtime.invoke_prompt(prompt=critique_prompt)

# Refine the plan based on critique
if "missing" in str(critique).lower() or "risk" in str(critique).lower():
    plan_response = await self._refine_plan(plan_response, str(critique))
```

### 4. Add Confidence Scoring from LLM

Instead of a fixed confidence, let the LLM self-assess:

```python
confidence_prompt = (
    f"Rate your confidence in this plan from 0.0 to 1.0:\n{plan_response}\n"
    f"Return ONLY a number."
)
confidence = float(await runtime.invoke_prompt(prompt=confidence_prompt))
```

---

## Expanding Sub-Agent Capabilities

### Pattern for All Sub-Agents

Every sub-agent inherits from `BaseAgent` (`src/agents/base.py`). To expand any sub-agent:

```python
class MyAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            agent_id="my_agent",
            description="What this agent does",
            system_prompt="You are an expert in...",
        )

    async def execute(self, message, context, params=None):
        # 1. Read plan context (from Plan Agent's working memory)
        plan = context.get_memory("plan_output")

        # 2. Build messages with plan context
        messages = self._build_messages(message, context)
        if plan:
            messages.insert(1, {
                "role": "system",
                "content": f"The Plan Agent provided this context:\n{plan}"
            })

        # 3. Call LLM, process, return AgentResult
        ...
```

### Example: Expanding Log Analysis Agent

Add structured log parsing and pattern detection:

```python
# src/agents/log_analysis_agent.py

import re
from collections import Counter

class LogAnalysisAgent(BaseAgent):
    """Enhanced log analysis with pattern detection."""

    # Add new capabilities
    ERROR_PATTERNS = {
        "null_pointer": r"NullPointerException|NoneType.*attribute",
        "timeout": r"TimeoutError|deadline exceeded|ETIMEDOUT",
        "auth_failure": r"401|403|Unauthorized|Forbidden|AuthenticationError",
        "oom": r"OutOfMemoryError|OOM|Cannot allocate memory",
        "connection": r"ConnectionRefused|ECONNREFUSED|Connection reset",
    }

    async def execute(self, message, context, params=None):
        # Classify the error type from log content
        detected_patterns = {}
        for name, pattern in self.ERROR_PATTERNS.items():
            if re.search(pattern, message, re.IGNORECASE):
                detected_patterns[name] = True

        # Include pattern detection in artifacts
        return AgentResult(
            agent_id=self.agent_id,
            content=f"Log analysis found: {list(detected_patterns.keys())}",
            confidence=0.9 if detected_patterns else 0.5,
            artifacts={
                "detected_patterns": detected_patterns,
                "severity": self._assess_severity(detected_patterns),
            },
        )

    def _assess_severity(self, patterns: dict) -> str:
        if "oom" in patterns or "null_pointer" in patterns:
            return "critical"
        if "auth_failure" in patterns:
            return "high"
        if "timeout" in patterns or "connection" in patterns:
            return "medium"
        return "low"
```

### Example: Expanding Code Research Agent

Add AST-based code analysis:

```python
# src/agents/code_research_agent.py

import ast

class CodeResearchAgent(BaseAgent):
    """Enhanced code research with AST analysis."""

    async def execute(self, message, context, params=None):
        # If code is provided, do structural analysis
        code_block = params.get("code") if params else None

        if code_block:
            analysis = self._analyze_code(code_block)
            return AgentResult(
                agent_id=self.agent_id,
                content=f"Code structure:\n{analysis}",
                confidence=0.9,
                artifacts={"ast_analysis": analysis},
            )

        # Otherwise, do keyword-based research
        ...

    def _analyze_code(self, code: str) -> dict:
        """Parse and analyze Python code structure."""
        try:
            tree = ast.parse(code)
            return {
                "functions": [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)],
                "classes": [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)],
                "imports": [
                    n.names[0].name for n in ast.walk(tree) if isinstance(n, ast.Import)
                ],
                "complexity": sum(
                    1 for n in ast.walk(tree)
                    if isinstance(n, (ast.If, ast.For, ast.While, ast.Try))
                ),
            }
        except SyntaxError:
            return {"error": "Could not parse code"}
```

### Example: Adding Tool Use to Sub-Agents

Give agents the ability to call external tools:

```python
# Add to any agent
from microsoft.agents.core import agent_function

class RemediationAgent(BaseAgent):
    """Remediation agent with tool use."""

    @agent_function(name="apply_patch", description="Apply a code patch")
    async def apply_patch(self, file_path: str, patch: str) -> str:
        """Tool that the LLM can invoke to apply a patch."""
        # Validate the patch
        # Apply it
        return f"Patch applied to {file_path}"

    @agent_function(name="run_tests", description="Run the test suite")
    async def run_tests(self, test_path: str = "tests/") -> str:
        """Tool that the LLM can invoke to run tests."""
        import subprocess
        result = subprocess.run(["pytest", test_path, "-v"], capture_output=True, text=True)
        return result.stdout
```

---

## Adding a Brand-New Agent

There are **two ways** to add a new agent:
- **Forge method** — drop a folder into `forge/agents/` or `forge/contrib/agents/` (YAML + Markdown, no Python required)
- **Code method** — create a Python class + register in the router and bootstrap

### Method 1: Forge Agent (Declarative — Recommended)

Create a new directory under `forge/agents/` (or `forge/contrib/agents/` for community contributions):

```
forge/agents/performance/
├── agent.yaml
├── prompts/
│   └── system.md
├── skills/
│   └── profile_performance.yaml
└── instructions/
    └── profiling_methods.md
```

#### `agent.yaml`

```yaml
id: performance_agent
name: Performance Agent
type: specialist
description: >
  Identifies bottlenecks, memory leaks, slow queries, and
  optimization opportunities in code and systems.
version: "1.0.0"

context_budget:
  max_input_tokens: 24000
  max_output_tokens: 12000
  strategy: priority
  priority_order: [system_prompt, current_message, profiling_data, recent_history]

subagents: []

prompts:
  system: system.md

skills:
  - profile_performance.yaml

instructions:
  - profiling_methods.md

tags: [performance, profiling, optimization, bottlenecks]
```

#### `prompts/system.md`

```markdown
You are a **Performance Profiler Agent**.
Analyze code and systems for performance bottlenecks, memory leaks,
slow queries, and optimization opportunities.

## Methodology
1. **Profile** — CPU, memory, I/O, and concurrency analysis
2. **Identify** — Bottlenecks, hot paths, and resource contention
3. **Recommend** — Specific optimizations with expected impact
4. **Validate** — Confirm fixes don't introduce regressions
```

#### `skills/profile_performance.yaml`

```yaml
name: profile_performance
description: "Analyze code or system for performance bottlenecks"
agent_type: performance
parameters:
  - name: target
    type: string
    description: "Code, endpoint, or system to profile"
    required: true
  - name: profile_type
    type: string
    description: "Type of profiling: cpu, memory, io, all"
    required: false
    default: "all"
```

The `ForgeLoader` will auto-discover this agent at startup — no Python changes needed. The skill is automatically exposed as an MCP tool.

### Method 1b: Forge Contribution (Runtime CRUD)

Use the `ContributionManager` to create an agent programmatically at runtime:

```python
from src.forge.contributions import ContributionManager

contrib = ContributionManager("forge")
contrib.create_agent(
    agent_id="performance_agent",
    manifest={
        "id": "performance_agent",
        "name": "Performance Agent",
        "type": "specialist",
        "description": "Performance profiling and optimization",
        "version": "1.0.0",
        "context_budget": {"max_input_tokens": 24000, "max_output_tokens": 12000, "strategy": "priority"},
        "tags": ["performance", "profiling"],
    },
    system_prompt="You are a Performance Profiler Agent...",
    author="team-x",
)
# Creates forge/contrib/agents/performance_agent/
# Audit-logged in forge/contrib/audit_log.yaml
```

### Method 2: Code Agent (Full Control)

For agents that need custom Python logic beyond LLM prompting:

#### Step 1: Create the Agent Class

```python
# src/agents/performance_agent.py
"""Performance Profiler Agent — identifies bottlenecks and optimization opportunities."""

from src.agents.base import BaseAgent
from src.orchestrator.context import AgentResult, ConversationContext

PERF_SYSTEM_PROMPT = """You are a Performance Profiler Agent.
You analyze code and systems for performance bottlenecks, memory leaks,
slow queries, and optimization opportunities.

Focus on: CPU profiling, memory profiling, I/O bottlenecks, database
query optimization, caching strategies, and concurrency issues."""


class PerformanceAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            agent_id="performance_agent",
            description="Performance profiling and optimization",
            system_prompt=PERF_SYSTEM_PROMPT,
        )

    async def execute(self, message, context, params=None):
        messages = self._build_messages(message, context)

        # Read plan context for coordinated execution
        plan = context.get_memory("plan_output")

        return AgentResult(
            agent_id=self.agent_id,
            content="Performance analysis: ...",
            confidence=0.85,
            artifacts={"metrics": {}, "recommendations": []},
        )
```

### Step 2: Register in the Router

```python
# src/orchestrator/router.py — add to AgentType enum
class AgentType(str, Enum):
    # ...existing agents...
    PERFORMANCE = "performance"

# Add keyword patterns
KEYWORD_ROUTES[AgentType.PERFORMANCE] = [
    r"\bperformance\b", r"\bprofile\b", r"\bbottleneck\b",
    r"\bslow\b", r"\blatency\b", r"\bmemory\s*leak\b",
    r"\boptimiz", r"\bcpu\b", r"\bthroughput\b",
]
```

### Step 3: Register in Bootstrap

```python
# src/main.py — add to bootstrap()
from src.agents.performance_agent import PerformanceAgent

agent_map[AgentType.PERFORMANCE] = (
    PerformanceAgent(),
    "Performance Agent",
    "Performance profiling and optimization"
)
```

### Step 4: Add a Forge Manifest (Optional but Recommended)

Create a `forge/agents/performance/` directory alongside the Python class for prompts and skills:

```yaml
# forge/agents/performance/agent.yaml
id: performance_agent
name: Performance Agent
type: specialist
description: "Performance profiling and optimization"
version: "1.0.0"
context_budget:
  max_input_tokens: 24000
  max_output_tokens: 12000
  strategy: priority
prompts:
  system: system.md
skills:
  - profile_performance.yaml
instructions:
  - profiling_methods.md
tags: [performance, profiling, optimization]
```

And the skill definition:

```yaml
# forge/agents/performance/skills/profile_performance.yaml
name: profile_performance
description: "Analyze code or system for performance bottlenecks"
agent_type: performance
parameters:
  - name: target
    type: string
    description: "Code, endpoint, or system to profile"
    required: true
  - name: profile_type
    type: string
    description: "Type of profiling: cpu, memory, io, all"
    required: false
    default: "all"
```

### Step 5: Add Tests

```python
# tests/test_performance_agent.py
import pytest
from src.agents.performance_agent import PerformanceAgent
from src.orchestrator.context import ConversationContext

class TestPerformanceAgent:
    @pytest.mark.asyncio
    async def test_executes_successfully(self):
        agent = PerformanceAgent()
        ctx = ConversationContext()
        result = await agent.execute("Profile the API endpoint", ctx)
        assert result.agent_id == "performance_agent"
        assert result.confidence > 0
```

---

## Adding New Skills & Workflows

### Adding a Skill

Skills are YAML files inside each agent's `skills/` directory (or `forge/shared/` or `forge/contrib/skills/`). They are auto-discovered by `ForgeLoader` and exposed as MCP tools.

```yaml
# forge/agents/security_sentinel/skills/analyze_dependencies.yaml
name: analyze_dependencies
description: "Analyze project dependencies for outdated or vulnerable packages"
agent_type: security_sentinel
parameters:
  - name: manifest_path
    type: string
    description: "Path to package manifest (requirements.txt, package.json, etc.)"
    required: true
  - name: check_vulnerabilities
    type: boolean
    description: "Whether to check for known CVEs"
    required: false
    default: true
```

Skills are auto-loaded at startup and exposed as MCP tools. You can also add skills via the `ContributionManager`:

```python
from src.forge.contributions import ContributionManager

contrib = ContributionManager("forge")
contrib.create_skill("analyze_dependencies", {
    "name": "analyze_dependencies",
    "description": "Analyze project dependencies for outdated or vulnerable packages",
    "parameters": [{"name": "manifest_path", "type": "string", "required": True}],
}, author="team-x")
# Creates forge/contrib/skills/analyze_dependencies.yaml
```

### Adding a Workflow

Workflows compose multiple agents into a pipeline. Place them in an agent's `workflows/` directory or in `forge/shared/workflows/`:

```yaml
# forge/shared/workflows/full_audit.yaml
name: full_audit
description: "Complete codebase audit: security + performance + code quality"
steps:
  - name: security_scan
    agent_type: security_sentinel
    prompt_template: "Scan for vulnerabilities: {target}"

  - name: code_review
    agent_type: code_research
    prompt_template: "Review code quality: {target}"
    # No depends_on — runs in parallel with security_scan

  - name: remediation_plan
    agent_type: remediation
    depends_on: [security_scan, code_review]
    prompt_template: "Generate fixes for: {security_scan.output} and {code_review.output}"
```

---

## Dynamic Contributions (CRUD)

The `ContributionManager` (`src/forge/contributions.py`) provides full CRUD for the `forge/contrib/` directory with automatic audit logging.

### Agent CRUD

```python
from src.forge.contributions import ContributionManager

contrib = ContributionManager("forge")

# Create
contrib.create_agent("my_agent", manifest={"id": "my_agent", "name": "My Agent", "type": "specialist", "description": "..."}, system_prompt="You are...", author="xavier")

# Update (manifest and/or prompt)
contrib.update_agent("my_agent", manifest={"id": "my_agent", "name": "My Agent v2", "type": "specialist", "description": "Updated"}, author="xavier")

# Delete (audit-logged, then files removed)
contrib.delete_agent("my_agent", author="xavier")
```

### Skill & Workflow CRUD

```python
# Skills
contrib.create_skill("my_skill", {"name": "my_skill", "description": "...", "parameters": [...]}, author="xavier")
contrib.delete_skill("my_skill", author="xavier")

# Workflows
contrib.create_workflow("my_workflow", {"name": "my_workflow", "steps": [...]}, author="xavier")
contrib.delete_workflow("my_workflow", author="xavier")
```

### Listing & Audit

```python
# List all contributions
contrib.list_contributions()
# → {"agents": ["my_agent"], "skills": ["my_skill.yaml"], "workflows": ["my_workflow.yaml"]}

# Get the audit trail
for entry in contrib.get_audit_log():
    print(f"{entry['timestamp']} | {entry['action']} | {entry['path']} | {entry['author']}")
```

Every create/update/delete is appended to `forge/contrib/audit_log.yaml`:

```yaml
entries:
  - timestamp: "2026-02-23T14:30:00"
    action: create_agent
    path: contrib/agents/my_agent
    author: xavier
    description: "Created agent: My Agent"
```

### Schema Validation

The `ContributionManager` validates manifests before accepting them:
- **Agents** must have `id`, `name`, `type`, `description`
- **Skills** must have `name`, `description`, `parameters`
- **Workflows** must have `name`, `steps`

Invalid contributions raise `ValidationError` with a descriptive message.

---

## Sub-Plan Agent (Dual HITL Resource Planning)

The **Sub-Plan Agent** sits between the Plan Agent and the task agents. It plans the minimum-viable prerequisite resources (infrastructure, connectors, APIs, authentication) that must be in place before task agents can execute.

### Why a Sub-Plan Layer?

Many requests require **prerequisite resources** that don't map to any single task agent. For example, *"create workspace connectors"* needs storage accounts, service principals, or API registrations deployed first. Without a Sub-Plan step, the Plan Agent would need to handle both strategic planning *and* infrastructure provisioning — violating single-responsibility.

### Architecture — Dual HITL Pipeline

```
Plan Agent output (recommended agents, strategy)
   ↓
┌──────────────────────────────────┐
│  Plan HITL (Phase A)             │
│  User accepts/rejects plan       │
│  suggestions and keywords        │
│  Timeout: 120s (fail-open)       │
└──────────────┬───────────────────┘
               ↓
Sub-Plan Agent (resource planner)
   ↓
┌──────────────────────────────────┐
│  Sub-Plan HITL (Phase B)         │
│  User accepts/rejects resources  │
│  Can override brief:             │
│  "minimum resources needed to    │
│   demonstrate the functionality" │
│  Timeout: 120s (fail-open)       │
└──────────────┬───────────────────┘
               ↓
Task Agents (parallel fan-out)
```

Both HITL gates use the `PlanSelector` class (`src/orchestrator/plan_selector.py`), which follows the same prepare → expose → wait → resolve pattern as the WorkIQ `WorkIQSelector`.

### PlanSelector API

```python
from src.orchestrator.plan_selector import PlanSelector

selector = PlanSelector(timeout_seconds=120)

# ── Phase A: Plan Review ──────────────────────────
# Prepare plan suggestions for HITL review
req = selector.prepare_plan_review(request_id, plan_suggestions)

# Check pending reviews
pending = selector.pending_plan_reviews()

# Resolve (user accepts some suggestions, rejects others)
selector.resolve_plan_review(request_id, accepted_indices=[0, 2])

# Wait for resolution (blocks up to timeout_seconds)
await selector.wait_for_plan_review(request_id)

# Get accepted suggestions
accepted = selector.accepted_plan_agents(request_id)

# ── Phase B: Resource Review ─────────────────────
# Prepare resource items for HITL review
req = selector.prepare_resource_review(request_id, resource_items)

# Resolve with optional user brief override
selector.resolve_resource_review(
    request_id,
    accepted_indices=[0, 1],
    user_brief="Deploy only the connector and storage account",
)

# Wait and retrieve results
await selector.wait_for_resource_review(request_id)
resources = selector.accepted_resources(request_id)
brief = selector.resource_brief(request_id)
```

### REST Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/plan/pending` | List pending Plan Agent suggestion reviews |
| POST | `/plan/accept` | Accept/reject plan suggestions (`{request_id, accepted_indices}`) |
| GET | `/sub-plan/pending` | List pending Sub-Plan resource reviews |
| POST | `/sub-plan/accept` | Accept/reject resources + optional `user_brief` override |

### Default Brief

If the user does not provide a custom brief, the Sub-Plan Agent uses:

> *"You should aim to create the minimum resources needed to demonstrate the functionality as an example."*

This ensures resource plans stay focused and avoid over-provisioning.

### Engine Integration

The `_run_sub_plan_pipeline()` method in `engine.py` orchestrates the full dual-HITL sequence:

1. **Plan HITL** — exposes plan suggestions, waits for user acceptance
2. **Sub-Plan execution** — runs the Sub-Plan Agent with accepted plan and user brief
3. **Sub-Plan HITL** — exposes resource items, waits for user acceptance
4. Stores results in working memory: `sub_plan_output`, `sub_plan_artifacts`, `resource_brief`

If no `PlanSelector` is configured, the Sub-Plan Agent still runs but without HITL gates (auto-accept all).

### Forge Manifest

```yaml
# forge/agents/sub_plan/agent.yaml
id: sub_plan
name: Sub-Plan Agent
type: specialist
description: >
  Plans prerequisite resources (infra, connectors, APIs, auth) required
  to demonstrate the planned functionality — minimum viable resources only.
version: "1.0.0"
context_budget:
  max_input_tokens: 20000
  max_output_tokens: 10000
  strategy: priority
prompts:
  system: system.md
skills:
  - plan_resources.yaml
instructions:
  - resource_guidelines.md
tags: [resources, infrastructure, connectors, deployment, prerequisites]
```

---

## WorkIQ Integration (2-Phase Human-in-the-Loop)

[Work IQ](https://www.npmjs.com/package/@microsoft/workiq) (`@microsoft/workiq`) brings **M365 organisational context** — emails, Teams messages, calendar events, SharePoint documents, and more — into the ProtoForge pipeline as a **pre-routing enrichment layer**. WorkIQ output feeds directly into the Intent Router through a 2-phase human-in-the-loop (HITL) pipeline.

### Why 2-Phase Human-in-the-Loop?

Work IQ queries return multiple result sections with varying relevance. The 2-phase HITL design addresses three critical concerns:

| Phase | Controls | Problem Solved |
|-------|----------|----------------|
| **Phase 1: Content Selection** | Which M365 sections enter the pipeline | Token waste, privacy — user sees and picks relevant sections |
| **Phase 2: Keyword Acceptance** | Which routing hints influence agent selection | Routing accuracy — user confirms which keywords should boost agent scores |

Both phases fail-open on timeout (2 min) — if the user doesn't respond, the query proceeds without enrichment.

### Architecture

```
┌──────────────┐     ┌──────────────────┐     ┌──────────────────┐
│  REST Client │────▶│  OrchestratorEngine  │──▶│  WorkIQClient    │
│  (Inspector) │     │  process_with_      │    │  (subprocess)    │
└──────┬───────┘     │  enrichment()       │    └────────┬─────────┘
       │             └──────┬──────────────┘             │
       │                    │                     ┌──────▼──────────┐
       │              ┌─────▼──────────┐          │  workiq ask     │
       │              │ WorkIQSelector │          │  CLI process    │
       ├─────────────▶│ Phase 1: HITL  │          └─────────────────┘
       │  (select)    │  select content│
       │              │                │
       ├─────────────▶│ Phase 2: HITL  │
       │  (accept-    │  accept keyword│
       │   hints)     │  hints         │
       │              └─────┬──────────┘
       │                    │ accepted hints
       │              ┌─────▼──────────┐
       │              │ IntentRouter   │
       │              │ route_with_    │
       │              │ context()      │
       │              └─────┬──────────┘
       │                    │ enriched routing
       │              ┌─────▼──────────┐
       │              │ Plan Agent →   │
       │              │ Sub-Agents →   │
       │              │ Aggregate      │
       │              └────────────────┘
```

**Components:**

| Component | File | Purpose |
|-----------|------|---------|
| `WorkIQClient` | `src/workiq/client.py` | Async subprocess wrapper for the `workiq ask` CLI — runs the query and parses JSON output into `WorkIQResult` objects |
| `WorkIQSelector` | `src/workiq/selector.py` | 2-phase HITL selector — Phase 1 manages content selection, Phase 2 manages keyword-hint acceptance |
| `WorkIQAgent` | `src/agents/workiq_agent.py` | Agent implementation — calls client, prepares selection, waits for user input, returns grounded result |
| `IntentRouter` | `src/orchestrator/router.py` | `extract_routing_keywords()` finds agent keywords in content; `route_with_context()` merges keyword + hint signals |
| `OrchestratorEngine` | `src/orchestrator/engine.py` | `process_with_enrichment()` orchestrates the full Phase 0→3 pipeline |
| REST endpoints | `src/server.py` | 6 endpoints for content selection + keyword-hint acceptance |

### The 2-Phase Selection Flow

#### Phase 0 — Query (automated)

The `WorkIQClient` runs `workiq ask "<query>"` as an async subprocess and parses the output:

```python
from src.workiq.client import WorkIQClient

client = WorkIQClient()
result = await client.ask("latest standup notes from Teams")
# result.ok → True
# result.content → raw text with sections
```

#### Phase 1 — Content Selection (HITL)

The `WorkIQSelector` receives the query results and stages them for user review:

```python
from src.workiq.selector import WorkIQSelector

selector = WorkIQSelector()
content_req = selector.prepare(workiq_result, request_id="abc123")
# content_req.resolved → False (waiting for user)
```

Via the REST API or Inspector UI, the user reviews sections and picks relevant ones:

```bash
# POST /workiq/select
{"request_id": "abc123", "selected_indices": [0, 2]}
```

Internally this calls `selector.resolve(request_id, [0, 2])` which:
- Concatenates selected sections' content
- Stores as `selected_content`
- Unblocks any waiting pipeline

#### Phase 2 — Keyword Extraction (automated)

The router scans the selected content for agent keywords:

```python
hints = router.extract_routing_keywords(selected_text)
# → [RoutingKeywordHint(agent_id="log_analysis", keyword="error", matched_text="...deploy error in..."),
#    RoutingKeywordHint(agent_id="security_sentinel", keyword="vulnerability", matched_text="...CVE found...")]
```

Each hint includes a ~60-character context snippet around the matched keyword.

#### Phase 2b — Keyword Acceptance (HITL)

Extracted keyword hints are surfaced for user review:

```python
hint_req = selector.prepare_routing_hints(hints, request_id="hint-456")
# If ≤1 hint, auto-resolved (no user interaction needed)
# If >1 hints, staged for HITL
```

Via the REST API:

```bash
# GET /workiq/routing-hints — see pending keyword hints
# POST /workiq/accept-hints
{"request_id": "hint-456", "accepted_indices": [0]}
```

Accepted hints boost the corresponding agent's score in the router.

#### Phase 3 — Enriched Routing

The router combines user-message keyword scoring with accepted hint boosts:

```python
routing = router.route_with_context(user_message, accepted_hints)
# routing.enrichment_applied → True
# routing.reasoning → "Matched: log_analysis(0.70, +enrichment: error), ..."
```

#### Full Pipeline (engine)

The `process_with_enrichment()` method orchestrates all phases:

```python
async def process_with_enrichment(self, user_message: str) -> str:
    # Phase 0: Query WorkIQ
    # Phase 1: HITL content selection (wait for user)
    # Phase 2: Extract keywords from selected content
    # Phase 2b: HITL keyword acceptance (wait for user)
    # Phase 3: route_with_context(message, accepted_hints)
    # → Plan Agent → Sub-Agents → Aggregate
```

If WorkIQ is not configured, or any phase fails, the engine transparently falls back to `process()` (standard routing).

### REST API Reference

| Method | Path | Phase | Description |
|--------|------|-------|-------------|
| POST | `/workiq/query` | 0 | Submit a query to Work IQ — returns `request_id` + sections |
| GET | `/workiq/pending` | 1 | List all queries waiting for content selection |
| POST | `/workiq/select` | 1 | Submit content selection (`request_id` + `selected_indices`) |
| GET | `/workiq/routing-hints` | 2 | List pending keyword-hint requests |
| POST | `/workiq/accept-hints` | 2b | Accept/reject keyword hints (`request_id` + `accepted_indices`) |
| POST | `/chat/enriched` | All | Full 2-phase enrichment pipeline in one call |

#### `POST /workiq/query`

```json
// Request
{"query": "find the email thread about the production incident"}

// Response
{
  "request_id": "abc123",
  "sections": [
    {"index": 0, "title": "Email: [P1] Production Incident — Auth Service", "preview": "Team, auth service is returning 500s since..."},
    {"index": 1, "title": "Teams: Incident War Room", "preview": "Just deployed a hotfix to..."},
    {"index": 2, "title": "Email: RE: [P1] Incident Postmortem", "preview": "Root cause was a misconfigured..."}
  ]
}
```

#### `POST /workiq/select`

```json
// Request
{"request_id": "abc123", "selected_indices": [0, 2]}

// Response
{"request_id": "abc123", "selected_content": "Email: [P1] Production Incident..."}
```

#### `GET /workiq/routing-hints`

```json
// Response
{
  "pending_hints": [
    {
      "request_id": "hint-456",
      "hints": [
        {"index": 0, "agent_id": "log_analysis", "keyword": "error", "matched_text": "...deploy error in the auth..."},
        {"index": 1, "agent_id": "security_sentinel", "keyword": "security", "matched_text": "...security review needed..."}
      ]
    }
  ]
}
```

#### `POST /workiq/accept-hints`

```json
// Request
{"request_id": "hint-456", "accepted_indices": [0]}

// Response
{"request_id": "hint-456", "accepted": [{"agent_id": "log_analysis", "keyword": "error"}]}
```

#### `POST /chat/enriched`

Runs the full 2-phase pipeline (Phase 0 → Phase 1 HITL → Phase 2 → Phase 2b HITL → Phase 3 → Plan → Aggregate). Falls back to standard routing if WorkIQ is unavailable.

```json
// Request
{"message": "What did the team discuss about the production outage?"}

// Response
{"response": "...aggregated agent response..."}
```

### How WorkIQ Output Feeds the Intent Router

The key concept: **Work IQ output is not automatically fed into agents.** It goes through two HITL gates before influencing routing:

1. **Phase 1 gate (content):** User selects which M365 sections are relevant — controls what organisational data enters the pipeline
2. **Phase 2 gate (keywords):** User accepts which routing keywords extracted from the content should boost agent scores — controls routing influence

**Enriched routing mechanics:**

- `extract_routing_keywords(selected_text)` scans the selected content against `_BUILTIN_KEYWORD_ROUTES` patterns and returns `RoutingKeywordHint` objects with agent_id, keyword, and ~60-char context snippet
- `route_with_context(message, accepted_hints)` scores the user message (Phase 1 signals) then adds boost points for each accepted hint (Phase 2 signals), with boost notation in the reasoning string
- The `enrichment_applied` flag on `RoutingDecision` indicates whether WorkIQ context influenced the routing

**Working memory keys set during enrichment:**

| Key | Content |
|-----|---------|
| `workiq_selected_content` | The user-selected text from Phase 1 |
| `workiq_accepted_hints` | List of `{agent, keyword}` dicts from Phase 2b |
| `workiq_content_pending` | Phase 1 request_id (while waiting) |
| `workiq_hints_pending` | Phase 2b request_id (while waiting) |

### Routing Keywords

The router triggers WorkIQ for queries matching these patterns:

```
workiq, work iq, organisational, organizational,
teams message, teams chat, teams discussion,
outlook email, calendar event, sharepoint,
m365, microsoft 365, office 365,
standup notes, meeting notes, who sent, who emailed
```

### Prerequisites

```bash
# Install Work IQ CLI globally (requires Node.js)
npm install -g @microsoft/workiq

# Accept the EULA (one-time, required)
workiq --acceptEula

# Verify installation
workiq --version

# Test a query
workiq ask "latest emails about project X"
```

If `workiq` is not installed, the WorkIQ agent gracefully degrades — it reports as unavailable in `protoforge status` and the router skips it.

### Privacy & Safety

- **2-gate HITL** — user explicitly controls both content sections (Phase 1) AND routing keywords (Phase 2)
- **Fail-open timeout** (2 min per phase) — pending queries expire without leaking data; on timeout, all options are accepted to avoid blocking
- **No persistent storage** — selected content lives only in the current orchestration context
- **Audit-ready** — `selector.pending_requests()` and `selector.pending_routing_hint_requests()` expose all in-flight state
- **Transparent fallback** — if WorkIQ fails or is not configured, the engine seamlessly falls back to standard keyword + LLM routing

---

## Extending the Codebase with GitHub Copilot CLI

GitHub Copilot CLI (`gh copilot`) lets you use AI directly from your terminal to explain code, suggest changes, and run AI-powered commands — without opening an editor.

### Setup

```powershell
# Install GitHub Copilot CLI extension
gh extension install github/gh-copilot

# Verify installation
gh copilot --version
```

### Common Commands for ProtoForge Development

#### Explain Code
```powershell
# Understand how the orchestrator works
gh copilot explain "Read src/orchestrator/engine.py and explain the Plan-first dispatch pattern"

# Understand the routing logic
gh copilot explain "How does intent routing work in src/orchestrator/router.py?"
```

#### Suggest Shell Commands
```powershell
# Let Copilot suggest how to run tests
gh copilot suggest "Run ProtoForge tests with coverage report"

# Let Copilot suggest how to profile the code
gh copilot suggest "Profile the ProtoForge orchestrator for memory usage"
```

#### AI-Powered Git Operations
```powershell
# Generate meaningful commit messages
gh copilot suggest "Write a git commit message for changes in src/agents/"

# Find commits that changed the router
gh copilot suggest "Find all git commits that modified the intent router"
```

---

## Multi-Model Code Review Workflow: Copilot CLI + Claude Opus 4.6 + Codex 5.3

One of the most powerful patterns for code quality is **getting critical feedback from multiple AI models concurrently**. Each model has different strengths:

| Model | Strength | Best For |
|-------|----------|----------|
| **Claude Opus 4.6** | Deep reasoning, nuanced analysis | Architecture review, logic correctness, edge cases |
| **Codex 5.3** | Code-native understanding | Implementation quality, performance, idioms |
| **GPT-4o** | Balanced general intelligence | Overall review, documentation, communication |

### Example: Reviewing the Orchestrator Engine

Open **two separate terminal windows** and fire both models at the same code:

#### Terminal 1 — Claude Opus 4.6 (Architecture & Logic Review)

```powershell
# Using GitHub Copilot CLI with Claude Opus 4.6
# Set your Anthropic API key
$env:ANTHROPIC_API_KEY = "your-key-here"

# Review the orchestrator engine for architectural issues
gh copilot explain @anthropic/claude-opus-4.6 "
Review src/orchestrator/engine.py critically:
1. Is the Plan-first dispatch pattern correctly implemented?
2. Are there race conditions in _fan_out() parallel execution?
3. Could _resolve_sub_agents() miss edge cases?
4. Is error handling comprehensive enough?
5. What are the scaling bottlenecks?
Give specific line-level feedback.
"
```

Or use **curl** to hit the Anthropic API directly for a deep review:

```powershell
# Terminal 1 — Claude Opus 4.6 deep review
$code = Get-Content src/orchestrator/engine.py -Raw
$body = @{
    model = "claude-opus-4.6"
    max_tokens = 4096
    messages = @(@{
        role = "user"
        content = @"
You are an expert code reviewer. Review this Python orchestrator engine critically.
Focus on: architecture correctness, error handling, race conditions, edge cases,
and scalability concerns. Be specific — reference function names and logic flows.

```python
$code
```

Provide:
1. Critical issues (must fix)
2. Warnings (should fix)
3. Suggestions (nice to have)
4. Architecture assessment (1-10 score with reasoning)
"@
    })
} | ConvertTo-Json -Depth 5

Invoke-RestMethod -Uri "https://api.anthropic.com/v1/messages" `
    -Method Post `
    -Headers @{
        "x-api-key" = $env:ANTHROPIC_API_KEY
        "anthropic-version" = "2023-06-01"
        "content-type" = "application/json"
    } `
    -Body $body | ForEach-Object { $_.content[0].text }
```

#### Terminal 2 — Codex 5.3 (Implementation & Performance Review)

```powershell
# Terminal 2 — Codex 5.3 implementation review
$env:OPENAI_API_KEY = "your-key-here"

$code = Get-Content src/orchestrator/engine.py -Raw
$body = @{
    model = "codex-5.3"
    messages = @(
        @{ role = "system"; content = "You are an expert Python code reviewer focused on implementation quality, performance, and idiomatic patterns." },
        @{ role = "user"; content = @"
Review this orchestrator engine for implementation quality:

```python
$code
```

Focus on:
1. Python idioms — is the code Pythonic?
2. Async patterns — is asyncio used correctly?
3. Performance — any unnecessary allocations, O(n^2) patterns, or blocking calls?
4. Type safety — are type hints correct and complete?
5. Error handling — what happens when things fail?
6. Testability — is the code easy to unit test?

Rate each area 1-10 and provide specific improvements.
"@ }
    )
    max_tokens = 4096
} | ConvertTo-Json -Depth 5

Invoke-RestMethod -Uri "https://api.openai.com/v1/chat/completions" `
    -Method Post `
    -Headers @{
        "Authorization" = "Bearer $($env:OPENAI_API_KEY)"
        "Content-Type" = "application/json"
    } `
    -Body $body | ForEach-Object { $_.choices[0].message.content }
```

### Compare & Merge Feedback

After both terminals complete, compare the outputs:

```powershell
# Save outputs from both terminals to files
# (Run after each terminal completes)

# Terminal 1 output → opus_review.md
# Terminal 2 output → codex_review.md

# Then use Copilot to synthesize
gh copilot explain "
Compare these two code reviews and create a unified action plan:
OPUS REVIEW: $(Get-Content opus_review.md -Raw)
CODEX REVIEW: $(Get-Content codex_review.md -Raw)
Prioritize items both models flagged as critical.
"
```

### Automating Multi-Model Review with a Script

Create a reusable review script:

```powershell
# review.ps1 — Multi-model code review script
param(
    [Parameter(Mandatory)]
    [string]$FilePath,

    [string]$Focus = "architecture, performance, correctness"
)

if (-not (Test-Path $FilePath)) {
    Write-Error "File not found: $FilePath"
    exit 1
}

$code = Get-Content $FilePath -Raw
$fileName = Split-Path $FilePath -Leaf

Write-Host "=== Multi-Model Code Review: $fileName ===" -ForegroundColor Cyan
Write-Host ""

# --- Claude Opus 4.6 Review ---
Write-Host "[1/2] Requesting Claude Opus 4.6 review..." -ForegroundColor Yellow

$opusBody = @{
    model = "claude-opus-4.6"
    max_tokens = 4096
    messages = @(@{
        role = "user"
        content = "Review this code critically. Focus on: $Focus`n`n``````python`n$code`n```````nProvide: Critical issues, Warnings, Suggestions, Score (1-10)."
    })
} | ConvertTo-Json -Depth 5

$opusReview = Invoke-RestMethod -Uri "https://api.anthropic.com/v1/messages" `
    -Method Post `
    -Headers @{
        "x-api-key" = $env:ANTHROPIC_API_KEY
        "anthropic-version" = "2023-06-01"
        "content-type" = "application/json"
    } `
    -Body $opusBody | ForEach-Object { $_.content[0].text }

Write-Host "--- Claude Opus 4.6 ---" -ForegroundColor Magenta
Write-Host $opusReview
Write-Host ""

# --- Codex 5.3 Review ---
Write-Host "[2/2] Requesting Codex 5.3 review..." -ForegroundColor Yellow

$codexBody = @{
    model = "codex-5.3"
    messages = @(
        @{ role = "system"; content = "Expert Python code reviewer. Focus on implementation quality." },
        @{ role = "user"; content = "Review critically. Focus: $Focus`n`n``````python`n$code`n```````nProvide: Critical issues, Warnings, Suggestions, Score (1-10)." }
    )
    max_tokens = 4096
} | ConvertTo-Json -Depth 5

$codexReview = Invoke-RestMethod -Uri "https://api.openai.com/v1/chat/completions" `
    -Method Post `
    -Headers @{
        "Authorization" = "Bearer $($env:OPENAI_API_KEY)"
        "Content-Type" = "application/json"
    } `
    -Body $codexBody | ForEach-Object { $_.choices[0].message.content }

Write-Host "--- Codex 5.3 ---" -ForegroundColor Green
Write-Host $codexReview
Write-Host ""

# --- Save reports ---
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$opusReview | Out-File "reviews/${fileName}_opus_${timestamp}.md"
$codexReview | Out-File "reviews/${fileName}_codex_${timestamp}.md"

Write-Host "=== Reviews saved to reviews/ ===" -ForegroundColor Cyan
```

Usage:

```powershell
# Review a specific file with both models
.\review.ps1 -FilePath src/orchestrator/engine.py

# Review with a specific focus
.\review.ps1 -FilePath src/agents/plan_agent.py -Focus "async patterns, error handling"

# Review the router
.\review.ps1 -FilePath src/orchestrator/router.py -Focus "regex correctness, edge cases"
```

### Quick One-Liner Reviews

For fast feedback on small code sections directly in your terminal:

```powershell
# Quick Opus review of a single function
Get-Content src/orchestrator/engine.py |
    Select-String -Pattern "async def process" -Context 0,40 |
    ForEach-Object { $_.Context.PostContext -join "`n" } |
    gh copilot explain "Review this async method for correctness"

# Quick check: are there any obvious bugs?
gh copilot explain "Are there any bugs in $(Get-Content src/orchestrator/router.py -Raw | Select-Object -First 50)?"

# Explain what a complex regex does
gh copilot explain "What does this regex match: \bfix\s.*\b(?:error|exception|bug|issue|problem)\b"
```

---

## Architecture Decision Records

### ADR-001: Plan-First Over Flat Dispatch

**Status:** Accepted  
**Context:** Need to coordinate multiple agents for complex requests  
**Decision:** Always run Plan Agent first, then fan out  
**Consequences:** Slightly higher latency (+1 LLM call), much better result quality  

### ADR-002: Claude Opus 4.6 as Default LLM

**Status:** Accepted  
**Context:** Need a default model that balances quality and reasoning depth  
**Decision:** Anthropic Claude Opus 4.6 as the default provider. Also supports Codex 5.3 and Gemini Pro 3.1 as first-class alternatives.  
**Consequences:** Requires at least one provider API key. Claude Opus 4.6 recommended for plan coordination; Codex 5.3 for code-heavy tasks; Gemini Pro 3.1 for cost-effective breadth.

### ADR-003: Keyword + LLM Two-Tier Routing

**Status:** Accepted  
**Context:** Pure LLM routing is slow and expensive; pure keyword routing misses nuance  
**Decision:** Fast keyword routing first, LLM fallback when confidence < 0.5  
**Consequences:** Sub-millisecond routing for clear intents, graceful degradation  

### ADR-004: MCP for Skills Distribution

**Status:** Accepted  
**Context:** Agent skills need to be accessible from VS Code Copilot, Claude Desktop, etc.  
**Decision:** Expose all skills as MCP tools via JSON-RPC  
**Consequences:** Any MCP-compatible client can use ProtoForge agents  

### ADR-005: YAML-Defined Skills and Workflows

**Status:** Accepted  
**Context:** Non-engineers need to define and modify agent capabilities  
**Decision:** Skills and workflows defined in YAML, auto-loaded at startup  
**Consequences:** Easy to add/modify without code changes, version-controlled  

### ADR-006: Declarative Forge Directory

**Status:** Accepted  
**Context:** Agent manifests, prompts, skills, instructions, and workflows were scattered — prompts inline in Python, skills in a flat `skills/` directory, no per-agent context budgets  
**Decision:** Centralize all agent definitions in a `forge/` directory tree with YAML manifests (`agent.yaml`) and Markdown prompts/instructions, auto-discovered by `ForgeLoader`  
**Consequences:** Clean separation of concerns. New agents can be added without Python code. All agent metadata is version-controlled. Trade-off: additional startup I/O for directory walking and YAML parsing  

### ADR-007: Context Window Budget Management

**Status:** Accepted  
**Context:** LLM context windows are finite and expensive. Without management, agents could consume unbounded tokens  
**Decision:** Centralized `ContextBudgetManager` with per-agent budgets (defined in `agent.yaml` or `_context_window.yaml` defaults), three truncation strategies (priority, sliding_window, summarize), and tiktoken-based counting  
**Consequences:** Predictable token usage per orchestration run. Global budget (128K) can be tuned. Dynamic rebalancing redistributes unused allocation. Requires tiktoken as optional dependency  

### ADR-008: Dynamic Contribution System with Audit Trail

**Status:** Accepted  
**Context:** Users and teams need to add agents, skills, and workflows without modifying core code  
**Decision:** `ContributionManager` provides CRUD operations on `forge/contrib/` with schema validation and a timestamped YAML audit log  
**Consequences:** Runtime extensibility. All changes are tracked (author, timestamp, action). Contributions can be reviewed via `get_audit_log()`. Contributed agents are isolated from core agents  

### ADR-009: Co-located Agent Resources

**Status:** Accepted  
**Context:** Each agent needs prompts, skills, instructions — having them in separate top-level directories makes it hard to reason about what belongs to which agent  
**Decision:** Each agent's resources are co-located in its own directory (`forge/agents/<name>/prompts/`, `skills/`, `instructions/`). Shared resources live in `forge/shared/`  
**Consequences:** Easy to understand what each agent has. Adding/removing an agent is a single directory operation. Shared resources reduce duplication across agents  

### ADR-010: WorkIQ Human-in-the-Loop Selection

**Status:** Superseded by ADR-011  
**Context:** Work IQ returns multiple M365 result sections per query. Injecting all sections blindly wastes tokens, degrades quality, and raises privacy concerns  
**Decision:** Human-in-the-loop selection — Work IQ output is staged for user review; only explicitly selected sections enter the agent pipeline. Implemented via `WorkIQSelector` with REST endpoints for the Inspector UI  
**Consequences:** Users control what organisational data enters the LLM. Adds one interaction step (query → select → proceed) but ensures relevance and privacy. Fail-open timeout (5 min) prevents stalled queries from blocking the pipeline  

### ADR-011: 2-Phase HITL — WorkIQ Enrichment Feeds Intent Router

**Status:** Accepted  
**Context:** ADR-010 only controlled which M365 content sections entered the pipeline — it didn't influence routing. Users wanted the ability to control which keywords extracted from WorkIQ content actually affect agent selection  
**Decision:** Extend the HITL pipeline to 2 phases: Phase 1 (content selection, as before) and Phase 2 (routing-keyword acceptance). `extract_routing_keywords()` scans selected content for agent keyword patterns, producing `RoutingKeywordHint` objects. These are staged for user review; accepted hints boost agent scores in `route_with_context()`. The full pipeline is exposed via `POST /chat/enriched`  
**Consequences:** Users now control both data and routing influence. Adds one additional HITL step. Auto-resolves when ≤1 hint (no user interaction needed). Both phases fail-open on timeout (2 min) to avoid pipeline stalls. Working memory tracks enrichment state (`workiq_selected_content`, `workiq_accepted_hints`)  

### ADR-012: Sub-Plan Agent with Dual HITL Gates

**Status:** Accepted  
**Context:** Complex requests (e.g., "create workspace connectors") require prerequisite resources (storage accounts, service principals, API registrations) that don't map to any task agent. The Plan Agent shouldn't handle both strategic planning AND infrastructure provisioning  
**Decision:** Insert a Sub-Plan Agent between Plan Agent and task agents. Both Plan output and Sub-Plan output go through HITL gates (`PlanSelector`). The Sub-Plan Agent defaults to a "minimum viable resources" brief, which users can override. Phase A: user accepts plan suggestions/keywords. Phase B: user accepts resource items and optionally provides a custom brief  
**Consequences:** Cleaner separation of concerns — Plan Agent strategizes, Sub-Plan Agent provisions. Two additional HITL steps (both fail-open at 120s). Users control both the plan AND the resource plan. Default brief prevents over-provisioning. Sub-Plan is excluded from task agent fan-out  

### ADR-013: Always-On Governance Guardian

**Status:** Accepted  
**Context:** Multi-agent orchestration can silently consume unbounded context tokens, agents can accumulate too many skills (violating single-responsibility), and the architectural boundary between agents/skills/sub-agents needs enforcement  
**Decision:** Implement a `GovernanceGuardian` with three enforcement pillars: (1) Context window governance with a 128K hard cap and 120K warning threshold triggering HITL decomposition, (2) Skill cap enforcement limiting agents to 4 skills with HITL-reviewed split suggestions, (3) Architectural principle enforcement auditing manifests for design violations. A `GovernanceSelector` provides HITL gates (ContextWindowReview + SkillCapReview) using the same prepare → expose → wait → resolve pattern as PlanSelector and WorkIQSelector. All HITL gates fail-open after 120s  
**Consequences:** Token costs are bounded and predictable. Agents stay focused (≤ 4 skills). Sub-agent creation is guided by governance. Adds governance check overhead to every dispatch (~1ms). 7 new REST endpoints for governance monitoring and HITL resolution. Governance rules are injected into every agent's system prompt via `forge/shared/instructions/governance_rules.md`  

---

## How to Add a Pre-Router Enrichment Source

ProtoForge's pre-router enrichment layer currently supports **WorkIQ** (M365 context). You can add additional data sources — call transcripts, Jira tickets, Slack threads, ServiceNow incidents, etc. — by following this pattern.

### Architecture: Where Enrichment Plugs In

```
User message
  → [YOUR ENRICHMENT SOURCE] ← Phase 0: query external system
    → Phase 1 (HITL): user selects relevant content
      → Phase 2: extract routing keywords
        → Phase 2b (HITL): user accepts keyword boosts
          → Intent Router (enriched)
            → Plan Agent → Sub-Agents → Aggregate
```

The enrichment layer sits **between** the HTTP endpoint and the Intent Router. It follows the same 2-phase HITL pattern as WorkIQ.

### Step-by-Step: Adding Call Transcript Enrichment

#### Step 1: Create the Client

Create a client that queries your data source and returns structured results:

```python
# src/transcripts/client.py
import asyncio
from dataclasses import dataclass
from typing import Optional

@dataclass
class TranscriptResult:
    ok: bool
    content: str
    sections: list[dict]  # [{"title": "...", "preview": "...", "text": "..."}]
    error: Optional[str] = None

class TranscriptClient:
    """Queries a call transcript store (e.g., Teams Recordings, Gong, Zoom)."""

    def __init__(self, api_endpoint: str, api_key: str):
        self._endpoint = api_endpoint
        self._key = api_key

    async def search(self, query: str) -> TranscriptResult:
        """Search transcripts matching the query."""
        # Replace with your actual API call:
        # response = await httpx.AsyncClient().get(self._endpoint, params={"q": query})
        # sections = self._parse_response(response.json())
        sections = []  # placeholder
        return TranscriptResult(
            ok=True,
            content="\n".join(s["text"] for s in sections),
            sections=sections,
        )
```

#### Step 2: Create the HITL Selector

Reuse the same prepare → wait → resolve pattern as `WorkIQSelector`:

```python
# src/transcripts/selector.py
import asyncio
from dataclasses import dataclass, field

@dataclass
class TranscriptSelectionRequest:
    request_id: str
    sections: list[dict]
    selected_indices: list[int] = field(default_factory=list)
    resolved: bool = False

class TranscriptSelector:
    """HITL gate for transcript content selection + keyword acceptance."""

    def __init__(self, timeout: float = 120.0):
        self._timeout = timeout
        self._pending: dict[str, TranscriptSelectionRequest] = {}
        self._events: dict[str, asyncio.Event] = {}

    def prepare(self, result: "TranscriptResult", request_id: str) -> TranscriptSelectionRequest:
        req = TranscriptSelectionRequest(request_id=request_id, sections=result.sections)
        self._pending[request_id] = req
        self._events[request_id] = asyncio.Event()
        return req

    def resolve(self, request_id: str, selected_indices: list[int]) -> None:
        req = self._pending[request_id]
        req.selected_indices = selected_indices
        req.resolved = True
        self._events[request_id].set()

    async def wait_for_selection(self, request_id: str) -> str:
        try:
            await asyncio.wait_for(self._events[request_id].wait(), self._timeout)
        except asyncio.TimeoutError:
            # Fail-open: accept all sections
            req = self._pending[request_id]
            req.selected_indices = list(range(len(req.sections)))
            req.resolved = True
        req = self._pending[request_id]
        return "\n".join(req.sections[i]["text"] for i in req.selected_indices)
```

#### Step 3: Wire into the Engine

Add an enrichment method in `OrchestratorEngine` following the WorkIQ pattern:

```python
# In src/orchestrator/engine.py — add new method

async def process_with_transcript_enrichment(self, user_message: str) -> str:
    """Enriched pipeline: call transcript → HITL → enriched routing."""
    if not self._transcript_client:
        return await self.process(user_message)

    self._context.add_user_message(user_message)
    if self._governance:
        self._governance.reset_run()

    # Phase 0: Query transcript store
    result = await self._transcript_client.search(user_message)
    if not result.ok or not result.sections:
        routing = self._router.route_by_keywords(user_message)
        return await self._process_after_routing(user_message, routing)

    # Phase 1 (HITL): User selects relevant transcript sections
    request_id = f"transcript-{id(result)}"
    self._transcript_selector.prepare(result, request_id)
    selected_text = await self._transcript_selector.wait_for_selection(request_id)

    # Phase 2: Extract routing keywords from selected content
    hints = self._router.extract_routing_keywords(selected_text)

    # Phase 2b (HITL): User accepts/rejects keyword hints
    # (reuse WorkIQSelector's hint mechanism or build your own)
    # ...

    # Phase 3: Enriched routing
    routing = self._router.route_with_context(user_message, accepted_hints)
    return await self._process_after_routing(user_message, routing)
```

#### Step 4: Add REST Endpoints

Add endpoints in `src/server.py` (or a new `src/server/transcripts.py` after P1-7):

```python
@app.post("/transcripts/query")
async def query_transcripts(body: dict):
    result = await transcript_client.search(body["query"])
    return {"request_id": "...", "sections": result.sections}

@app.get("/transcripts/pending")
async def pending_transcript_selections():
    return {"pending": list(transcript_selector._pending.values())}

@app.post("/transcripts/select")
async def select_transcript_sections(body: dict):
    transcript_selector.resolve(body["request_id"], body["selected_indices"])
    return {"status": "resolved"}
```

#### Step 5: Register in Bootstrap

Wire the client and selector into `bootstrap()` in `src/main.py`:

```python
# In bootstrap(), after WorkIQ setup:
transcript_client = TranscriptClient(
    api_endpoint=settings.transcript_api_endpoint,
    api_key=settings.transcript_api_key,
)
transcript_selector = TranscriptSelector(timeout=120.0)
orchestrator.set_transcript_enrichment(transcript_client, transcript_selector)
```

#### Step 6: Add Config

Add settings to `src/config.py`:

```python
class Settings(BaseSettings):
    # ... existing settings ...
    transcript_api_endpoint: str = ""
    transcript_api_key: str = ""
```

### Key Design Rules for Enrichment Sources

| Rule | Why |
|------|-----|
| **Always 2-phase HITL** | User controls both content (Phase 1) and routing influence (Phase 2) |
| **Fail-open on timeout** | Enrichment should never block the pipeline — fall back to standard routing |
| **No persistent storage** | Selected content lives only in the current orchestration context |
| **Reuse `extract_routing_keywords()`** | The router already knows agent keyword patterns — reuse for all enrichment sources |
| **Reuse `route_with_context()`** | Pass accepted hints to the same boost mechanism — consistent scoring |
| **Add working memory keys** | Store enrichment state (e.g., `transcript_selected_content`, `transcript_accepted_hints`) in `ConversationContext` |
| **Graceful degradation** | If the data source is unavailable, silently fall back to `process()` |

### Enrichment Sources You Could Add

| Source | Client | What It Brings |
|--------|--------|---------|
| **Call transcripts** (Teams/Zoom/Gong) | `TranscriptClient` | Meeting context, decisions, action items |
| **Jira / Azure DevOps** | `JiraClient` | Issue context, sprint data, acceptance criteria |
| **Slack / Teams messages** | `SlackClient` | Team discussions, decisions, context |
| **ServiceNow** | `ServiceNowClient` | Incident details, change requests, CMDB data |
| **Confluence / SharePoint** | `WikiClient` | Documentation, runbooks, architecture docs |
| **PagerDuty / OpsGenie** | `AlertClient` | Alert history, escalation context |

All follow the same pattern: Client → Selector (HITL) → Keywords → Router → Plan.

---

## Further Reading

- [Microsoft Agent Framework (Python)](https://learn.microsoft.com/en-us/agent-framework/overview/?pivots=programming-language-python)
- [Microsoft Agent Framework — Concepts](https://learn.microsoft.com/en-us/agent-framework/concepts/)
- [Model Context Protocol Spec](https://modelcontextprotocol.io/)
- [GitHub Copilot CLI](https://docs.github.com/en/copilot/github-copilot-in-the-cli)
- [Anthropic Claude API](https://docs.anthropic.com/en/docs)
- [OpenAI Codex API](https://platform.openai.com/docs)
