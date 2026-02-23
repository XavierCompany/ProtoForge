# ProtoForge — Multi-Agent Orchestrator

A production-ready multi-agent orchestrator built on the [Microsoft Agent Framework (Python)](https://learn.microsoft.com/en-us/agent-framework/overview/?pivots=programming-language-python) with a declarative `forge/` agent ecosystem, MCP skills distribution, context window management, dynamic contributions, and platform-agnostic LLM support.

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                    HTTP Server (FastAPI)                       │
│  /chat   /mcp   /agents   /skills   /workflows   /inspector  │
└─────────────────────────┬────────────────────────────────────┘
                          │
                 ┌────────▼────────┐
                 │   Orchestrator   │ ← Intent Router (keyword + LLM)
                 │     Engine       │
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
                 │  (HITL)    │   Human-in-the-loop selection
                 └────────────┘

   ─── Plan-First Flow ────────────────────────────────
   User Message
     → Orchestrator (intent routing)
       → Plan Agent (ALWAYS first — produces strategy)
         → Sub-Agents (parallel fan-out based on plan)
           → Aggregated Response
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
| **WorkIQ** | Sub-Agent | M365 organisational context via [Work IQ](https://www.npmjs.com/package/@microsoft/workiq) with human-in-the-loop selection |

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

## Agent Registry / Catalog

```bash
# List agents
GET /agents

# Search skills
GET /skills

# Health check with full status
GET /health
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/chat` | Send message to orchestrator |
| POST | `/mcp` | MCP JSON-RPC endpoint |
| GET | `/agents` | List registered agents |
| GET | `/skills` | List available skills |
| GET | `/workflows` | List workflows |
| POST | `/workflows/run` | Execute a workflow |
| GET | `/health` | Health check |
| GET | `/inspector` | Agent Inspector dashboard |
| POST | `/workiq/query` | Query Work IQ for organisational context |
| GET | `/workiq/pending` | List pending HITL selections |
| POST | `/workiq/select` | Submit user selection for a pending query |

## WorkIQ Integration (Human-in-the-Loop)

ProtoForge integrates [Work IQ](https://www.npmjs.com/package/@microsoft/workiq) (`@microsoft/workiq`) to surface M365 organisational context — emails, Teams messages, calendar events, SharePoint documents, and more — directly into the agent pipeline.

### How It Works

WorkIQ queries return **multiple result sections** (e.g., 3 emails, 2 Teams chats, 1 calendar event). Instead of blindly feeding everything into the LLM, ProtoForge uses a **human-in-the-loop (HITL) selection flow** — the user picks which sections are relevant before they enter the orchestrator:

```
User query ("find the Teams discussion about the outage")
  → WorkIQ CLI (`workiq ask "..."`) — returns ranked sections
    → HITL: user reviews sections, selects the relevant ones
      → Selected content injected into agent pipeline
        → Plan Agent plans with real organisational context
```

### HITL Selection Flow (REST API)

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

#### Step 2 — Review Pending Selections

```bash
curl http://localhost:8080/workiq/pending
```

Returns all queries awaiting user selection.

#### Step 3 — Select Sections

Pick the sections you want (by index):

```bash
curl -X POST http://localhost:8080/workiq/select \
  -H "Content-Type: application/json" \
  -d '{"request_id": "abc123", "selected_indices": [0, 1]}'
```

**Response** — the selected content, ready for the pipeline:

```json
{
  "request_id": "abc123",
  "selected_content": "Teams: Daily Standup 2026-02-24\nDiscussed prod deploy...\n\nTeams: Standup Recap Thread\nAction items from standup..."
}
```

The selected content is then available to the WorkIQ agent (`/chat` with a workiq-routed query) as grounded organisational context.

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

- **Fail-open timeout** — if the user doesn't select within 5 minutes, the query expires (no data leaks into the pipeline)
- **User controls what enters the LLM** — only explicitly selected sections are used
- **Audit-friendly** — `workiq_selector.pending_requests` shows all in-flight selections

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests (110 tests)
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
│   ├── server.py               # FastAPI HTTP server
│   ├── agents/                 # 8 agent implementations (Python)
│   │   ├── base.py
│   │   ├── plan_agent.py
│   │   ├── log_analysis_agent.py
│   │   ├── code_research_agent.py
│   │   ├── remediation_agent.py
│   │   ├── knowledge_base_agent.py
│   │   ├── data_analysis_agent.py
│   │   ├── security_sentinel_agent.py
│   │   └── workiq_agent.py     #   WorkIQ agent (M365 HITL)
│   ├── forge/                  # ★ Forge runtime modules
│   │   ├── loader.py           #   ForgeLoader — discovers forge/ tree
│   │   ├── context_budget.py   #   ContextBudgetManager — token budgets
│   │   └── contributions.py    #   ContributionManager — CRUD + audit
│   ├── orchestrator/           # Core orchestration engine
│   │   ├── engine.py           #   Plan-first dispatch + fan-out
│   │   ├── router.py           #   Intent classification
│   │   └── context.py          #   Shared conversation context
│   ├── mcp/                    # MCP protocol server
│   │   ├── server.py           #   MCP request handler
│   │   ├── protocol.py         #   MCP message types
│   │   └── skills.py           #   YAML skill loader
│   ├── workiq/                 # WorkIQ integration (M365 context)
│   │   ├── client.py           #   Async subprocess wrapper for `workiq ask`
│   │   └── selector.py         #   Human-in-the-loop selection manager
│   ├── registry/               # Agent catalog & workflows
│       ├── catalog.py          #   Agent registration & discovery
│       └── workflows.py        #   Workflow bundling & execution
└── tests/
    ├── test_forge.py           # 34 tests — loader, context budget, contributions
    ├── test_router.py
    ├── test_orchestrator.py
    ├── test_mcp.py
    ├── test_registry.py
    └── test_workiq.py          # 37 tests — client, selector, agent, routing
```

## Developer Guide

See **[GUIDE.md](GUIDE.md)** for:
- Why this architecture was chosen (Plan-first vs flat dispatch)
- The Forge ecosystem in depth (manifests, context budgets, contributions)
- How to expand Plan Agent and sub-agent capabilities
- How to add brand-new agents via code or the `forge/contrib/` system
- Adding new skills, workflows, and shared resources
- **WorkIQ integration** — querying M365 context, human-in-the-loop selection flow, REST API usage
- **Multi-model code review with GitHub Copilot CLI** — run Claude Opus 4.6 and Codex 5.3 in parallel terminals for critical feedback
- Architecture Decision Records (ADRs)
