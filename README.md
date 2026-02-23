# ProtoForge — Multi-Agent Orchestrator

A production-ready multi-agent orchestrator built on the [Microsoft Agent Framework (Python)](https://learn.microsoft.com/en-us/agent-framework/overview/?pivots=programming-language-python) with a declarative `forge/` agent ecosystem, MCP skills distribution, context window management, dynamic contributions, and platform-agnostic LLM support.

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                     HTTP Server (FastAPI)                         │
│  /chat  /chat/enriched  /mcp  /agents  /skills  /workflows       │
│  /workiq/*  /inspector                                           │
└──────────────────────────┬───────────────────────────────────────┘
                           │
            ┌──────────────▼──────────────┐
            │      WorkIQ Pre-Router      │ ← (optional enrichment)
            │  ┌──────────────────────┐   │
            │  │ Phase 0: workiq ask  │   │  Query M365 context
            │  │ Phase 1: HITL select │   │  User picks content
            │  │ Phase 2: extract kw  │   │  Keyword hints
            │  │ Phase 2b: HITL hints │   │  User accepts keywords
            │  └──────────┬───────────┘   │
            └─────────────┼───────────────┘
                          │  routing hints
                 ┌────────▼────────┐
                 │   Orchestrator   │ ← Intent Router (keyword + LLM)
                 │     Engine       │   + WorkIQ enrichment hints
                 └────────┬────────┘
                          │  ALWAYS first
                 ┌────────▼────────┐
                 │   Plan Agent    │ ← Top-level coordinator
                 │  (Coordinator)  │    Analyzes, strategizes,
                 │                 │    identifies sub-agents
                 └──┬──┬──┬──┬──┬─┘
        ┌───────────┘  │  │  │  └───────────┐
        ▼              ▼  │  ▼              ▼
   ┌──────────┐ ┌─────────┐│┌─────────────────┐
   │   Log    │ │  Code   │││   Remediation   │
   │ Analysis │ │Research ││└─────────────────┘
   └──────────┘ └─────────┘│
        ▼              ▼   │        ▼
   ┌──────────┐ ┌──────────┐ ┌────────────────┐
   │Knowledge │ │  Data    │ │   Security     │
   │  Base    │ │ Analysis │ │   Sentinel     │
   └──────────┘ └──────────┘ └────────────────┘
                      │
                 ┌────▼───────┐
                 │  WorkIQ    │ ← M365 organisational context
                 │  (HITL)    │   2-phase human-in-the-loop
                 └────────────┘

   ─── Standard Flow (/chat) ──────────────────────────
   User Message
     → Intent Router (keyword + LLM)
       → Plan Agent (ALWAYS first — produces strategy)
         → Sub-Agents (parallel fan-out based on plan)
           → Aggregated Response
   ────────────────────────────────────────────────────

   ─── Enriched Flow (/chat/enriched) ─────────────────
   User Message
     → WorkIQ query (M365 context)
       → HITL Phase 1: user selects content sections
         → Keyword extraction from selected content
           → HITL Phase 2: user accepts/rejects keywords
             → Intent Router (keyword + hints + LLM)
               → Plan Agent → Sub-Agents → Aggregated Response
   ────────────────────────────────────────────────────

┌─────────────────┐  ┌─────────────────┐  ┌──────────────────┐
│  Forge Loader   │  │  Context Budget  │  │  Contribution    │
│  (Agent YAML)   │  │  Manager         │  │  Manager (CRUD)  │
└─────────────────┘  └─────────────────┘  └──────────────────┘
┌─────────────────┐  ┌─────────────────┐  ┌──────────────────┐
│  MCP Server     │  │  Agent Catalog  │  │  Workflow Engine  │
│  (Skills Dist.) │  │  (Registry)     │  │  (Bundling)       │
└─────────────────┘  └─────────────────┘  └──────────────────┘
```

## Plan-First Agent Architecture

The **Plan Agent** is the top-level coordinator. Every request goes through Plan Agent first, which:
1. Analyzes the request scope and produces a strategic plan
2. Identifies which sub-agents to invoke
3. Provides structured context for downstream execution
4. Sub-agents execute in parallel, then results are aggregated

## 8 Specialized Agents (1 Coordinator + 7 Sub-Agents)

| Agent | Role | Purpose |
|-------|------|---------|
| **Plan** | Coordinator | Top-level: analyzes requests, produces plans, identifies sub-agents |
| **Log Analysis** | Sub-Agent | Log parsing, error analysis, stack traces, crash investigation |
| **Code Research** | Sub-Agent | Code search, function lookup, implementation understanding |
| **Remediation** | Sub-Agent | Bug fixes, patches, hotfixes, workarounds |
| **Knowledge Base** | Sub-Agent | Documentation retrieval, how-to guides, RAG |
| **Data Analysis** | Sub-Agent | Metrics, trends, charts, statistical analysis |
| **Security Sentinel** | Sub-Agent | Vulnerability scanning, CVE lookup, compliance audits |
| **WorkIQ** | Sub-Agent + Pre-Router Enrichment | M365 organisational context via [Work IQ](https://www.npmjs.com/package/@microsoft/workiq) with 2-phase HITL — content selection + routing-keyword acceptance |

## Agent Registry / Catalog

The **Agent Catalog** (`src/registry/catalog.py`) is the central registry that tracks all available agents, their capabilities, skills, health metrics, and dependencies. It supports runtime registration, search, and metric tracking.

### Managing Sub-Agents via the Catalog

```python
from src.registry.catalog import AgentCatalog, AgentRegistration, CatalogEntry

catalog = AgentCatalog(storage_path=Path(".forge_catalog"))

# Register an agent
catalog.register_agent(AgentRegistration(
    agent_type="log_analysis",
    name="Log Analysis Agent",
    description="Expert in parsing and diagnosing application logs",
    version="1.0.0",
    skills=["analyze_logs"],
    tags=["logs", "errors", "debugging"],
))

# List all active agents
for agent in catalog.list_agents(status="active"):
    print(f"{agent.name} — {agent.description} (skills: {agent.skills})")

# Search agents by tag
security_agents = catalog.list_agents(tag="security")

# Track agent metrics (latency, errors)
catalog.update_agent_metrics("log_analysis", latency_ms=145.2, is_error=False)

# Unregister an agent
catalog.unregister_agent("my_old_agent")
```

### Skill Catalog

Skills discovered from `forge/` are auto-populated into the catalog at startup. You can also manage them at runtime:

```python
# Add a skill to the catalog
catalog.add_to_catalog(CatalogEntry(
    skill_name="analyze_dependencies",
    description="Analyze project dependencies for vulnerabilities",
    agent_type="security_sentinel",
    version="1.0.0",
    tags=["security", "dependencies"],
))

# Install / uninstall skills
catalog.install_skill("analyze_dependencies")
catalog.uninstall_skill("analyze_dependencies")

# Search the skill catalog
results = catalog.search_catalog(query="security", installed_only=True)

# Bulk-populate from ForgeLoader skills
catalog.populate_from_skills(forge_registry.skills)
```

### REST API for the Catalog

```bash
# List all registered agents with capabilities
GET /agents

# List all available skills
GET /skills

# Full system status including catalog stats
GET /health
```

The catalog persists to `catalog.json` on disk and auto-loads at startup.

## The Forge Ecosystem

The `forge/` directory is the declarative heart of ProtoForge — every agent, prompt, skill, workflow, and context budget is defined in YAML and Markdown, auto-discovered at startup by `ForgeLoader`.

### Forge Directory Layout

```
forge/
├── _registry.yaml              # Master registry of all agents
├── _context_window.yaml        # Global token budget configuration
├── plan/                       # Plan Agent (coordinator)
│   ├── agent.yaml              #   Manifest: id, subagents, context_budget
│   ├── prompts/                #   System & strategy prompts (.md)
│   ├── skills/                 #   plan_task, identify_agents, build_strategy
│   ├── instructions/           #   routing_rules, coordination
│   └── workflows/              #   plan_and_execute.yaml
├── agents/                     # 7 specialist agents
│   ├── log_analysis/           #   agent.yaml + prompts/ + skills/ + instructions/
│   ├── code_research/
│   ├── remediation/
│   ├── knowledge_base/
│   ├── data_analysis/
│   ├── security_sentinel/
│   └── workiq/                 #   WorkIQ agent (M365 context + HITL selection)
├── shared/                     # Cross-agent resources
│   ├── prompts/                #   error_handling.md, output_format.md
│   ├── instructions/           #   quality_standards.md, security_baseline.md
│   └── workflows/              #   code_review.yaml, incident_response.yaml
└── contrib/                    # Dynamic contributions (CRUD via API)
    ├── audit_log.yaml          #   Timestamped audit trail
    ├── agents/                 #   Community-contributed agents
    ├── skills/                 #   Community-contributed skills
    └── workflows/              #   Community-contributed workflows
```

### Agent Manifests (`agent.yaml`)

Each agent is fully described by a YAML manifest:

```yaml
id: plan_agent
name: Plan Agent
type: coordinator          # coordinator | specialist
description: >
  Top-level coordinator that analyzes every incoming request,
  produces a strategic execution plan, and identifies which
  specialist sub-agents should be invoked downstream.
version: "1.0.0"
context_budget:
  max_input_tokens: 24000
  max_output_tokens: 12000
  strategy: priority       # priority | sliding_window | summarize
subagents:
  - log_analysis
  - code_research
  - remediation
  - knowledge_base
  - data_analysis
  - security_sentinel
prompts:
  system: system.md
skills:
  - plan_task.yaml
  - identify_agents.yaml
  - build_strategy.yaml
instructions:
  - routing_rules.md
  - coordination.md
```

### Context Window Management

Token budgets are centrally configured in `forge/_context_window.yaml` and enforced by the `ContextBudgetManager`:

- **Global budget:** 128K tokens per orchestration run (32K reserved for Plan, 16K for aggregation)
- **Per-agent budgets:** Defined in each `agent.yaml` or defaults by type (specialist: 16K/8K, coordinator: 24K/12K)
- **Strategies:** `priority` (keep highest-priority content), `sliding_window` (keep most recent), `summarize` (LLM-compress)
- **Token counting:** tiktoken (`cl100k_base`) with character-estimate fallback
- **Dynamic scaling:** Rebalances unused budget across agents when overflow detected

### Dynamic Contributions

The `ContributionManager` provides full CRUD for adding agents, skills, and workflows at runtime:

```python
from src.forge.contributions import ContributionManager

contrib = ContributionManager("forge")

# Create a new agent
contrib.create_agent("my_agent", manifest={...}, system_prompt="...", author="team-x")

# Add a skill
contrib.create_skill("my_skill", skill_def={...}, author="team-x")

# All changes are audit-logged in forge/contrib/audit_log.yaml
```

## Platform-Agnostic LLM Support

Works with any LLM provider:

| Provider | Models | Notes |
|----------|--------|-------|
| **Anthropic** | `claude-opus-4.6` (default), `claude-sonnet-4.6` | **Recommended** — highest quality reasoning |
| **Azure AI Foundry** | `gpt-5.3-codex` | Best quality/cost/throughput on Azure |
| **OpenAI** | `gpt-4o`, `codex-5.3` | Direct OpenAI API access |
| **Google** | `gemini-3-pro`, `gemini-3.1-pro` | Google AI Studio / Vertex AI |

> **Default:** Anthropic Claude Opus 4.6 — set `DEFAULT_LLM_PROVIDER=anthropic` or provide `ANTHROPIC_API_KEY` to use automatically.

## Quick Start

```bash
# 1. Clone and install
cd ProtoForge
pip install -e ".[dev]"

# 2. Configure
cp .env.example .env
# Edit .env with your credentials

# 3. Run the server
protoforge serve
# → http://localhost:8080/inspector (Agent Inspector)

# 4. Or use interactive chat
protoforge chat

# 5. Check status
protoforge status
```

## MCP Integration

ProtoForge exposes all agent skills via the **Model Context Protocol (MCP)**, making them available to any MCP-compatible AI client (VS Code Copilot, Claude Desktop, etc.).

Skills are auto-discovered from `forge/` at startup — every `skills/*.yaml` inside an agent directory or `forge/shared/` or `forge/contrib/` is collected and exposed as an MCP tool.

```json
// POST /mcp
{
  "jsonrpc": "2.0",
  "method": "tools/list",
  "id": 1
}

// Response: all agent skills as MCP tools
```

### Skills (YAML-defined)

Skills live inside each agent's directory under `forge/`:

```yaml
# forge/plan/skills/plan_task.yaml
name: plan_task
description: "Break down a complex task into actionable steps"
agent_type: plan
parameters:
  - name: task_description
    type: string
    description: "The task to plan"
    required: true
```

## Workflow Bundling

Compose multi-agent workflows from YAML definitions:

```yaml
# forge/shared/workflows/incident_response.yaml
name: incident_response
description: "Full incident response: logs → code → security → diagnose → fix"
steps:
  - name: analyze_logs
    agent_type: log_analysis
    prompt_template: "Analyze logs for incident: {incident_description}"
  - name: research_code
    agent_type: code_research
    depends_on: [analyze_logs]
  - name: security_check
    agent_type: security_sentinel
    depends_on: [analyze_logs]
  - name: diagnose
    agent_type: knowledge_base
    depends_on: [research_code, security_check]
  - name: generate_fix
    agent_type: remediation
    depends_on: [diagnose]
```

Steps with no dependencies run in parallel. The workflow engine handles dependency ordering automatically.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/chat` | Send message to orchestrator (standard routing) |
| POST | `/chat/enriched` | Send message with WorkIQ 2-phase HITL enrichment |
| POST | `/mcp` | MCP JSON-RPC endpoint |
| GET | `/agents` | List registered agents (catalog) |
| GET | `/skills` | List available skills (catalog) |
| GET | `/workflows` | List workflows |
| POST | `/workflows/run` | Execute a workflow |
| GET | `/health` | Health check (includes catalog stats) |
| GET | `/inspector` | Agent Inspector dashboard |
| POST | `/workiq/query` | Query Work IQ for organisational context |
| GET | `/workiq/pending` | List pending Phase 1 HITL content selections |
| POST | `/workiq/select` | Submit user content selection for a pending query |
| GET | `/workiq/routing-hints` | List pending Phase 2 HITL keyword hints |
| POST | `/workiq/accept-hints` | Accept/reject routing keyword hints |

## WorkIQ Integration (2-Phase Human-in-the-Loop)

ProtoForge integrates [Work IQ](https://www.npmjs.com/package/@microsoft/workiq) (`@microsoft/workiq`) to surface M365 organisational context — emails, Teams messages, calendar events, SharePoint documents, and more — as a **pre-routing enrichment layer** that feeds selected keywords directly into the Intent Router.

### How It Works — 2-Phase HITL Enrichment

WorkIQ output flows through two human-in-the-loop gates before influencing routing:

```
User query ("find the Teams discussion about the outage")
  → Phase 0: WorkIQ CLI (`workiq ask "..."`) — returns ranked sections
    → Phase 1 (HITL): user selects relevant content sections
      → Phase 2: extract routing keywords from selected content
        → Phase 2b (HITL): user accepts/rejects keyword hints
          → Phase 3: enriched Intent Router (message + accepted keywords)
            → Plan Agent → Sub-Agents → Aggregated Response
```

### Phase 1 — Content Selection (HITL)

#### Step 1 — Query Work IQ

```bash
curl -X POST http://localhost:8080/workiq/query \
  -H "Content-Type: application/json" \
  -d '{"query": "latest standup notes from Teams"}'
```

**Response** — a `request_id` and ranked sections:

```json
{
  "request_id": "abc123",
  "sections": [
    {"index": 0, "title": "Teams: Daily Standup 2026-02-24", "preview": "Discussed prod deploy..."},
    {"index": 1, "title": "Teams: Standup Recap Thread", "preview": "Action items from standup..."},
    {"index": 2, "title": "Email: Standup Summary", "preview": "Hi team, here are the notes..."}
  ]
}
```

#### Step 2 — Review Pending & Select Sections

```bash
# Check pending selections
curl http://localhost:8080/workiq/pending

# Select sections
curl -X POST http://localhost:8080/workiq/select \
  -H "Content-Type: application/json" \
  -d '{"request_id": "abc123", "selected_indices": [0, 1]}'
```

### Phase 2 — Routing Keyword Acceptance (HITL)

After Phase 1, the router extracts keyword hints from the selected content (e.g., "error" → log_analysis, "security" → security_sentinel). These are surfaced for user review:

#### Step 3 — Review Keyword Hints

```bash
curl http://localhost:8080/workiq/routing-hints
```

**Response:**

```json
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

#### Step 4 — Accept Keyword Hints

```bash
curl -X POST http://localhost:8080/workiq/accept-hints \
  -H "Content-Type: application/json" \
  -d '{"request_id": "hint-456", "accepted_indices": [0]}'
```

**Response:**

```json
{
  "request_id": "hint-456",
  "accepted": [{"agent_id": "log_analysis", "keyword": "error"}]
}
```

Accepted keywords boost the corresponding agent's score in the Intent Router, influencing which sub-agents are dispatched.

### Enriched Routing via `/chat/enriched`

The **`POST /chat/enriched`** endpoint runs the full 2-phase pipeline automatically:

```bash
curl -X POST http://localhost:8080/chat/enriched \
  -H "Content-Type: application/json" \
  -d '{"message": "What did the team discuss about the production outage?"}'
```

This triggers Phase 0 → Phase 1 (HITL) → Phase 2 → Phase 2b (HITL) → Phase 3 enriched routing → Plan Agent → Sub-Agents → Response. If WorkIQ is not configured or fails, it falls back to the standard `/chat` pipeline.

### Prerequisites

```bash
# Install Work IQ CLI globally
npm install -g @microsoft/workiq

# Accept the EULA (one-time)
workiq --acceptEula

# Verify
workiq --version
```

### Privacy & Control

- **2-gate HITL** — user explicitly controls both content sections AND routing keywords
- **Fail-open timeout** (2 min per phase) — pending selections expire without leaking data
- **No persistent storage** — selected content lives only in the current orchestration context
- **Audit-friendly** — `pending_requests`, `pending_routing_hint_requests` expose all in-flight state

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests (136 tests)
pytest

# Run with coverage
pytest --cov=src

# Lint (ruff, line-length 120)
ruff check src/

# Type check
mypy src/
```

## Project Structure

```
ProtoForge/
├── pyproject.toml              # Project config & dependencies
├── GUIDE.md                    # Developer guide (architecture, extending, ADRs)
├── README.md                   # This file
├── forge/                      # ★ Declarative agent ecosystem
│   ├── _registry.yaml          #   Master agent registry
│   ├── _context_window.yaml    #   Token budget configuration
│   ├── plan/                   #   Plan Agent (coordinator)
│   │   ├── agent.yaml
│   │   ├── prompts/
│   │   ├── skills/
│   │   ├── instructions/
│   │   └── workflows/
│   ├── agents/                 #   7 specialist agents
│   │   ├── log_analysis/
│   │   ├── code_research/
│   │   ├── remediation/
│   │   ├── knowledge_base/
│   │   ├── data_analysis/
│   │   ├── security_sentinel/
│   │   └── workiq/             #   WorkIQ (M365 context + HITL)
│   ├── shared/                 #   Cross-agent prompts, instructions, workflows
│   │   ├── prompts/
│   │   ├── instructions/
│   │   └── workflows/
│   └── contrib/                #   Dynamic contributions (CRUD + audit)
│       ├── audit_log.yaml
│       └── README.md
├── src/
│   ├── main.py                 # Entry point & bootstrap
│   ├── config.py               # Settings (pydantic-settings + ForgeConfig)
│   ├── server.py               # FastAPI HTTP server (14 endpoints)
│   ├── agents/                 # 8 agent implementations (Python)
│   │   ├── base.py             #   BaseAgent + BaseAgent.from_manifest()
│   │   ├── generic.py          #   GenericAgent (forge-contributed agents)
│   │   ├── plan_agent.py
│   │   ├── log_analysis_agent.py
│   │   ├── code_research_agent.py
│   │   ├── remediation_agent.py
│   │   ├── knowledge_base_agent.py
│   │   ├── data_analysis_agent.py
│   │   ├── security_sentinel_agent.py
│   │   └── workiq_agent.py     #   WorkIQ agent (M365 2-phase HITL)
│   ├── forge/                  # ★ Forge runtime modules
│   │   ├── loader.py           #   ForgeLoader — discovers forge/ tree
│   │   ├── context_budget.py   #   ContextBudgetManager — token budgets
│   │   └── contributions.py    #   ContributionManager — CRUD + audit
│   ├── orchestrator/           # Core orchestration engine
│   │   ├── engine.py           #   Plan-first dispatch + WorkIQ enrichment
│   │   ├── router.py           #   Intent Router (keyword + LLM + enrichment)
│   │   └── context.py          #   Shared conversation context
│   ├── mcp/                    # MCP protocol server
│   │   ├── server.py           #   MCP request handler
│   │   ├── protocol.py         #   MCP message types
│   │   └── skills.py           #   YAML skill loader
│   ├── workiq/                 # WorkIQ integration (M365 context)
│   │   ├── client.py           #   Async subprocess wrapper for `workiq ask`
│   │   └── selector.py         #   2-phase HITL selector (content + keywords)
│   └── registry/               # ★ Agent Catalog & workflows
│       ├── catalog.py          #   AgentCatalog — registration, search, metrics
│       └── workflows.py        #   Workflow bundling & execution
└── tests/
    ├── test_forge.py           # 34 tests — loader, context budget, contributions
    ├── test_router.py          # 22 tests — keywords, enriched routing, hints
    ├── test_orchestrator.py    # 19 tests — engine, fan-out, aggregation
    ├── test_mcp.py             # 14 tests — protocol, server, skills
    ├── test_registry.py        # 10 tests — catalog, workflows
    └── test_workiq.py          # 37 tests — client, selector, agent, enrichment
```

## Developer Guide

See **[GUIDE.md](GUIDE.md)** for:
- Why this architecture was chosen (Plan-first vs flat dispatch)
- The Forge ecosystem in depth (manifests, context budgets, contributions)
- How to expand Plan Agent and sub-agent capabilities
- How to add brand-new agents via code or the `forge/contrib/` system
- Adding new skills, workflows, and shared resources
- **WorkIQ integration** — 2-phase HITL enrichment pipeline, routing keyword acceptance, REST API usage
- **Agent Registry / Catalog** — managing sub-agents, skill catalog, health metrics, persistence
- **Multi-model code review with GitHub Copilot CLI** — run Claude Opus 4.6 and Codex 5.3 in parallel terminals for critical feedback
- Architecture Decision Records (ADRs)
