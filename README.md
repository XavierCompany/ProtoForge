# ProtoForge — Multi-Agent Orchestrator

A production-ready multi-agent orchestrator with MCP skills distribution, agent registry/catalog, workflow bundling, and platform-agnostic LLM support.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    HTTP Server (FastAPI)                      │
│  /chat   /mcp   /agents   /skills   /workflows   /inspector │
└────────────────────────┬────────────────────────────────────┘
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

  ─── Plan-First Flow ────────────────────────────────
  User Message
    → Orchestrator (intent routing)
      → Plan Agent (ALWAYS first — produces strategy)
        → Sub-Agents (parallel fan-out based on plan)
          → Aggregated Response
  ────────────────────────────────────────────────────

┌─────────────────┐  ┌─────────────────┐  ┌──────────────────┐
│  MCP Server     │  │  Agent Catalog  │  │ Workflow Engine   │
│  (Skills Dist.) │  │  (Registry)     │  │ (Bundling)        │
└─────────────────┘  └─────────────────┘  └──────────────────┘
```

## Plan-First Agent Architecture

The **Plan Agent** is the top-level coordinator. Every request goes through Plan Agent first, which:
1. Analyzes the request scope and produces a strategic plan
2. Identifies which sub-agents to invoke
3. Provides structured context for downstream execution
4. Sub-agents execute in parallel, then results are aggregated

## 7 Specialized Agents (1 Coordinator + 6 Sub-Agents)

| Agent | Role | Purpose |
|-------|------|---------|
| **Plan** | Coordinator | Top-level: analyzes requests, produces plans, identifies sub-agents |
| **Log Analysis** | Sub-Agent | Log parsing, error analysis, stack traces, crash investigation |
| **Code Research** | Sub-Agent | Code search, function lookup, implementation understanding |
| **Remediation** | Sub-Agent | Bug fixes, patches, hotfixes, workarounds |
| **Knowledge Base** | Sub-Agent | Documentation retrieval, how-to guides, RAG |
| **Data Analysis** | Sub-Agent | Metrics, trends, charts, statistical analysis |
| **Security Sentinel** | Sub-Agent | Vulnerability scanning, CVE lookup, compliance audits |

## Platform-Agnostic LLM Support

Works with any LLM provider:
- **Azure AI Foundry** — `gpt-5.3-codex` (recommended for quality/cost/throughput)
- **OpenAI** — GPT-4o, Codex 5.3
- **Anthropic** — Claude Opus 4 (`claude-opus-4-0625`, default), Claude Sonnet 4
- **Google** — Gemini 3 Pro, Gemini 3.1 Pro

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

```json
// POST /mcp
{
  "jsonrpc": "2.0",
  "method": "tools/list",
  "id": 1
}

// Response: all 7 agent skills as MCP tools
```

### Skills (YAML-defined)

Skills are defined in `skills/*.yaml` and auto-loaded:

```yaml
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
# workflows/incident_response.yaml
name: incident_response
steps:
  - name: analyze_logs
    agent_type: log_analysis
    prompt_template: "Analyze logs: {incident_description}"
  - name: research_code
    agent_type: code_research
    depends_on: [analyze_logs]
  - name: generate_fix
    agent_type: remediation
    depends_on: [research_code]
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

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run with coverage
pytest --cov=src

# Lint
ruff check src/

# Type check
mypy src/
```

## Project Structure

```
ProtoForge/
├── pyproject.toml          # Project config & dependencies
├── .env.example            # Environment template
├── skills/                 # MCP skill definitions (YAML)
│   ├── plan.yaml
│   ├── log_analysis.yaml
│   ├── code_research.yaml
│   ├── remediation.yaml
│   ├── knowledge_base.yaml
│   ├── data_analysis.yaml
│   └── security_scan.yaml
├── workflows/              # Workflow bundle definitions
│   ├── incident_response.yaml
│   └── code_review.yaml
├── src/
│   ├── main.py             # Entry point & bootstrap
│   ├── config.py           # Settings (pydantic-settings)
│   ├── server.py           # FastAPI HTTP server
│   ├── agents/             # 7 specialized subagents
│   │   ├── base.py
│   │   ├── plan_agent.py
│   │   ├── log_analysis_agent.py
│   │   ├── code_research_agent.py
│   │   ├── remediation_agent.py
│   │   ├── knowledge_base_agent.py
│   │   ├── data_analysis_agent.py
│   │   └── security_sentinel_agent.py
│   ├── orchestrator/       # Core orchestration engine
│   │   ├── engine.py       # Switch-case router + fan-out
│   │   ├── router.py       # Intent classification
│   │   └── context.py      # Shared conversation context
│   ├── mcp/                # MCP protocol server
│   │   ├── server.py       # MCP request handler
│   │   ├── protocol.py     # MCP message types
│   │   └── skills.py       # YAML skill loader
│   └── registry/           # Agent catalog & workflows
│       ├── catalog.py      # Agent registration & discovery
│       └── workflows.py    # Workflow bundling & execution
└── tests/
    ├── test_router.py
    ├── test_orchestrator.py
    ├── test_mcp.py
    └── test_registry.py
```
