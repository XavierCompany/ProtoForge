# Building Agents with ProtoForge

> Practical guide: how to build a new specialist agent and wire it to an LLM
> hosted in Azure AI Foundry. Covers **conversing with the Plan Agent**,
> creating the agent from scratch, and running a full end-to-end pipeline.

---

## Table of Contents

1. [How ProtoForge Builds and Runs Agents](#how-protoforge-builds-and-runs-agents)
2. [Talking to the Plan Agent](#talking-to-the-plan-agent)
3. [Tutorial: Build a "Cost Advisor" Agent with AI Foundry](#tutorial-build-a-cost-advisor-agent-with-ai-foundry)
   - [Step 1 — Create the Forge Manifest](#step-1--create-the-forge-manifest)
   - [Step 2 — Register in the Agent Registry](#step-2--register-in-the-agent-registry)
   - [Step 3 — Add Routing Patterns](#step-3--add-routing-patterns)
   - [Step 4 — Implement the Python Agent](#step-4--implement-the-python-agent)
   - [Step 5 — Register in Bootstrap](#step-5--register-in-bootstrap)
   - [Step 6 — Write Tests](#step-6--write-tests)
   - [Step 7 — Verify Budget Math](#step-7--verify-budget-math)
   - [Step 8 — Run and Validate](#step-8--run-and-validate)
4. [Configuring Azure AI Foundry as the LLM Provider](#configuring-azure-ai-foundry-as-the-llm-provider)
5. [Wiring LLM Calls into Your Agent](#wiring-llm-calls-into-your-agent)
6. [The Full Pipeline: From User Message to Agent Response](#the-full-pipeline-from-user-message-to-agent-response)
7. [Advanced: Forge-Only Agents (No Python)](#advanced-forge-only-agents-no-python)
8. [Quick Reference](#quick-reference)

---

## How ProtoForge Builds and Runs Agents

Every user message flows through the same pipeline:

```
User → /chat endpoint → IntentRouter (keywords + LLM classification)
  → Plan Agent (produces strategy + identifies specialists)
    → Plan HITL gate (human reviews the plan)
      → Sub-Plan Agent (plans prerequisite resources)
        → Sub-Plan HITL gate (human reviews resource plan)
          → Fan-out to ≤3 specialist agents in parallel
            → Aggregate all results → User
```

**Key concept:** You never call a specialist agent directly. The Plan Agent
decides which specialists to invoke based on the user's intent. Your job
when building a new agent is to:

1. Declare *what* the agent does (forge manifest)
2. Tell the router *when* to suggest it (keyword patterns)
3. Implement *how* it does it (Python class)

---

## Talking to the Plan Agent

The Plan Agent is always the first agent invoked. When you send a message
to ProtoForge, the Plan Agent analyses it and recommends which specialists
to dispatch. Here's what that conversation looks like:

### Example: Requesting a cost analysis

**You send** (via `POST /chat`):
```json
{
  "message": "Analyze the cloud spend for our production environment and suggest optimizations"
}
```

**Plan Agent responds:**
```text
**Plan Agent — Coordination Plan**

1. Understand Requirements — Parse scope of cloud cost analysis
2. Identify Components — Map cloud services, resource groups, billing data
3. Design Solution — Propose cost optimization strategy
4. Delegate to Sub-Agents — Invoke [cost_advisor, data_analysis] for specialized work

Recommended agents: cost_advisor (primary), data_analysis (secondary)
Risks: Requires access to billing APIs
Success criteria: Actionable cost reduction recommendations with estimated savings
```

**Plan HITL gate:** You review the plan in the dashboard (`/inspector`)
and approve or modify it. Then the Sub-Plan Agent runs, followed by the
specialists.

### Using the enriched route

If WorkIQ is configured, use `POST /chat/enriched` instead. This adds
organisational context from Microsoft 365 (calendars, emails, documents)
before routing, with two additional HITL gates for content selection and
keyword approval.

---

## Tutorial: Build a "Cost Advisor" Agent with AI Foundry

We'll build a `cost_advisor` specialist that uses an LLM hosted in Azure AI
Foundry to analyse cloud spending and recommend optimisations.

### Step 1 — Create the Forge Manifest

Create the directory structure:

```
forge/agents/cost_advisor/
├── agent.yaml
├── instructions/
│   └── cost_guidelines.md
├── prompts/
│   └── system.md
└── skills/
    └── analyze_costs.yaml
```

**`forge/agents/cost_advisor/agent.yaml`**:
```yaml
id: cost_advisor
name: Cost Advisor Agent
type: specialist
description: >
  Analyses cloud infrastructure spending, identifies over-provisioned
  resources, and recommends cost optimisations with estimated savings.

version: "1.0.0"

context_budget:
  max_input_tokens: 15000
  max_output_tokens: 7000   # 22K envelope
  strategy: priority
  priority_order:
    - system_prompt
    - current_message
    - billing_data
    - recent_history

subagents: []

prompts:
  system: system.md

skills:
  - analyze_costs.yaml

instructions:
  - cost_guidelines.md

tags: [cost, optimization, billing, cloud, infrastructure]
```

**`forge/agents/cost_advisor/prompts/system.md`**:
```markdown
You are the Cost Advisor Agent — an expert in cloud infrastructure cost
optimisation.

Your responsibilities:
1. Analyse billing data and resource utilisation metrics
2. Identify over-provisioned, idle, or wasteful resources
3. Recommend right-sizing, reserved instances, and spot/preemptible usage
4. Estimate potential savings per recommendation
5. Prioritise recommendations by impact and implementation effort

Output format:
- Executive summary with total potential savings
- Ranked list of recommendations (HIGH/MEDIUM/LOW impact)
- Per-recommendation: current cost, projected cost, effort, risk
- Implementation timeline

Be precise. Base every recommendation on data, not assumptions.
```

**`forge/agents/cost_advisor/skills/analyze_costs.yaml`**:
```yaml
name: analyze_costs
description: "Analyze cloud spending data and produce cost optimisation recommendations"
agent_type: cost_advisor
parameters:
  - name: billing_data
    type: string
    description: "Raw billing or usage data to analyse"
    required: true
  - name: time_range
    type: string
    description: "Time range for analysis (e.g., 'last 30 days')"
    required: false
```

### Step 2 — Register in the Agent Registry

Add to `forge/_registry.yaml` under `agents:`:

```yaml
agents:
  # ... existing agents ...
  cost_advisor:
    path: agents/cost_advisor/
    type: specialist
    description: "Analyses cloud spending and recommends cost optimisations"
```

### Step 3 — Add Routing Patterns

In `src/orchestrator/router.py`, add to the `AgentType` enum and keyword routes:

```python
# In AgentType enum:
COST_ADVISOR = "cost_advisor"

# In _BUILTIN_KEYWORD_ROUTES:
AgentType.COST_ADVISOR: [
    r"\bcost[s]?\b",
    r"\bbilling\b",
    r"\bspend\b",
    r"\boptimi[sz]e\b",
    r"\bbudget\b",
    r"\bpricing\b",
    r"\breserved\s*instance[s]?\b",
    r"\bright[\-\s]?siz\w*\b",
],
```

### Step 4 — Implement the Python Agent

Create `src/agents/cost_advisor_agent.py`:

```python
"""Cost Advisor Agent — cloud spending analysis and optimisation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from src.agents.base import BaseAgent
from src.orchestrator.context import AgentResult, ConversationContext

if TYPE_CHECKING:
    from src.forge.loader import AgentManifest

logger = structlog.get_logger(__name__)

_DEFAULT_COST_PROMPT = """You are the Cost Advisor Agent.
Analyse cloud spending and recommend optimisations."""


class CostAdvisorAgent(BaseAgent):
    """Analyses cloud infrastructure spending and recommends optimisations.

    When wired to an LLM (Azure AI Foundry), the agent sends billing data
    and usage context to the model for deep analysis. The placeholder
    implementation below demonstrates the response structure.
    """

    def __init__(
        self,
        agent_id: str = "cost_advisor",
        description: str = "Cloud spending analysis and cost optimisation",
        system_prompt: str = _DEFAULT_COST_PROMPT,
        *,
        manifest: AgentManifest | None = None,
    ) -> None:
        super().__init__(
            agent_id=agent_id,
            description=description,
            system_prompt=system_prompt,
            manifest=manifest,
        )

    async def execute(
        self,
        message: str,
        context: ConversationContext,
        params: dict[str, Any] | None = None,
    ) -> AgentResult:
        logger.info("cost_advisor_executing", message_length=len(message))

        # Build LLM message list (system prompt + history + user message)
        messages = self._build_messages(message, context)

        # ── LLM call goes here ──────────────────────────────────
        # See "Wiring LLM Calls" section below for the actual
        # Azure AI Foundry integration pattern.
        #
        # response = await self._call_llm(messages)
        # ────────────────────────────────────────────────────────

        # Placeholder response (replace with LLM output)
        response = (
            "**Cost Advisor Report**\n\n"
            f"Analysis of: {message[:100]}...\n\n"
            "**Status:** LLM backend not yet connected.\n"
            "Connect Azure AI Foundry to enable deep cost analysis.\n"
        )

        return AgentResult(
            agent_id=self.agent_id,
            content=response,
            confidence=0.5,
            artifacts={"analysis_type": "cost_optimisation"},
        )
```

### Step 5 — Register in Bootstrap

In `src/main.py`, add the import and mapping:

```python
# At the top, add:
from src.agents.cost_advisor_agent import CostAdvisorAgent

# In _SPECIALISED_CLASSES, add:
AgentType.COST_ADVISOR: CostAdvisorAgent,

# In _default_agents, add:
AgentType.COST_ADVISOR: (
    CostAdvisorAgent,
    "Cost Advisor Agent",
    "Cloud spending analysis and cost optimisation",
),
```

### Step 6 — Write Tests

Create `tests/test_cost_advisor.py`:

```python
"""Tests for the Cost Advisor Agent."""

from __future__ import annotations

import pytest

from src.agents.cost_advisor_agent import CostAdvisorAgent
from src.orchestrator.context import ConversationContext


@pytest.fixture
def cost_advisor():
    return CostAdvisorAgent()


@pytest.fixture
def context():
    return ConversationContext()


class TestCostAdvisorAgent:
    @pytest.mark.asyncio
    async def test_basic_execution(self, cost_advisor, context):
        result = await cost_advisor.execute("Analyze our AWS bill", context)
        assert result.agent_id == "cost_advisor"
        assert result.content
        assert result.confidence > 0

    @pytest.mark.asyncio
    async def test_artifacts_contain_analysis_type(self, cost_advisor, context):
        result = await cost_advisor.execute("Optimize cloud costs", context)
        assert result.artifacts["analysis_type"] == "cost_optimisation"

    def test_agent_id(self, cost_advisor):
        assert cost_advisor.agent_id == "cost_advisor"

    def test_from_manifest(self):
        """Verify manifest-driven construction works."""
        from unittest.mock import MagicMock

        manifest = MagicMock()
        manifest.id = "cost_advisor"
        manifest.name = "Cost Advisor Agent"
        manifest.description = "Cloud cost analysis"
        manifest.resolved_prompts = {"system": "You are a cost advisor."}

        agent = CostAdvisorAgent.from_manifest(manifest)
        assert agent.agent_id == "cost_advisor"
        assert agent.system_prompt == "You are a cost advisor."
```

### Step 7 — Verify Budget Math

The context window constraint is:

```
plan(32K) + sub_plan(20K) + max 3 specialists ≤ 128K
```

Your agent has a 22K budget (15K input + 7K output). Worst case with 3
specialists running in parallel:

```
32K + 20K + 22K + 25K + 25K = 124K ✅ (4K headroom)
```

If you need a larger budget, read `forge/_context_window.yaml` and
recalculate. See the [budget-calculator skill](/.github/skills/budget-calculator/SKILL.md).

### Step 8 — Run and Validate

```powershell
# Run tests
.venv\Scripts\python.exe -m pytest tests/test_cost_advisor.py -v --tb=short

# Run all tests (should still pass)
.venv\Scripts\python.exe -m pytest tests/ -q --tb=short

# Lint
.venv\Scripts\python.exe -m ruff check src/agents/cost_advisor_agent.py tests/test_cost_advisor.py

# Start the server and verify the agent appears
.venv\Scripts\python.exe -m src.main serve
# Then visit http://localhost:8080/agents to see cost_advisor listed
```

---

## Configuring Azure AI Foundry as the LLM Provider

ProtoForge supports four LLM providers. To use Azure AI Foundry:

### 1. Set environment variables

Create a `.env` file in the project root (or set system env vars):

```env
# Azure AI Foundry configuration
DEFAULT_LLM_PROVIDER=azure_ai_foundry
AZURE_AI_FOUNDRY_ENDPOINT=https://your-resource.services.ai.azure.com
AZURE_AI_FOUNDRY_MODEL=gpt-5.3-codex
AZURE_AI_FOUNDRY_API_VERSION=2026-01-01

# Authentication (Azure Default Credential recommended)
AUTH_METHOD=azure_default
```

### 2. Authenticate

Azure AI Foundry uses `DefaultAzureCredential` by default. Ensure you are
logged in:

```powershell
az login
# Or set a service principal:
# $env:AZURE_CLIENT_ID = "..."
# $env:AZURE_CLIENT_SECRET = "..."
# $env:AZURE_TENANT_ID = "..."
```

### 3. Verify

```python
from src.config import get_settings

settings = get_settings()
print(settings.llm.active_provider)      # → azure_ai_foundry
print(settings.llm.azure_endpoint)       # → https://your-resource...
print(settings.llm.azure_model)          # → gpt-5.3-codex
```

### Provider priority

If `DEFAULT_LLM_PROVIDER` is not set, ProtoForge auto-detects based on
available credentials:

| Priority | Provider | Credential Check |
|----------|----------|------------------|
| 1 | Explicit `DEFAULT_LLM_PROVIDER` | Always wins |
| 2 | Anthropic (Claude Opus 4.6) | `ANTHROPIC_API_KEY` set |
| 3 | Azure AI Foundry | `AZURE_AI_FOUNDRY_ENDPOINT` set |
| 4 | OpenAI (Codex 5.3) | `OPENAI_API_KEY` set |
| 5 | Google (Gemini Pro 3.1) | `GOOGLE_API_KEY` set |
| Default | Anthropic | Fallback when nothing configured |

---

## Wiring LLM Calls into Your Agent

> **Current state:** ProtoForge agents return structured placeholder responses.
> The TODO item P0-5 ("Wire LLM calls to Microsoft Agent Framework") is the
> next priority. Below is the integration pattern to follow when connecting.

### Pattern: Azure AI Foundry with `azure-ai-inference`

```python
from azure.ai.inference.aio import ChatCompletionsClient
from azure.identity.aio import DefaultAzureCredential

from src.config import get_settings


async def call_foundry(messages: list[dict[str, str]]) -> str:
    """Call Azure AI Foundry and return the assistant message."""
    settings = get_settings()
    credential = DefaultAzureCredential()

    async with ChatCompletionsClient(
        endpoint=settings.llm.azure_endpoint,
        credential=credential,
    ) as client:
        response = await client.complete(
            model=settings.llm.azure_model,
            messages=messages,
        )

    return response.choices[0].message.content
```

### Integrating into your agent's `execute()`:

```python
async def execute(self, message, context, params=None):
    messages = self._build_messages(message, context)

    # Call Azure AI Foundry
    llm_response = await call_foundry(messages)

    return AgentResult(
        agent_id=self.agent_id,
        content=llm_response,
        confidence=0.8,
        artifacts={"provider": "azure_ai_foundry"},
    )
```

### Budget-aware calling

The `ContextBudgetManager` enforces per-agent token limits. When
`truncate_on_dispatch` is enabled (default), the engine truncates your
input before calling `execute()`. To manually check:

```python
from src.forge.context_budget import ContextBudgetManager

budget = ContextBudgetManager(context_config)
budget.allocate("cost_advisor")
truncated_messages = budget.truncate("cost_advisor", messages)
```

---

## The Full Pipeline: From User Message to Agent Response

Here's what happens end-to-end when a user sends "Optimize our cloud costs":

```
1. POST /chat {"message": "Optimize our cloud costs"}

2. IntentRouter.route("Optimize our cloud costs")
   → Keyword match: "cost" → cost_advisor, "optimize" → cost_advisor
   → RoutingDecision(primary=cost_advisor, confidence=0.9)

3. Engine always invokes Plan Agent first
   → Plan Agent builds strategy, recommends [cost_advisor, data_analysis]

4. Plan HITL gate
   → Dashboard shows plan for human review
   → Human approves (or modifies, or timeout auto-approves)

5. Sub-Plan Agent runs
   → Plans prerequisite resources (e.g., billing API access)
   → Sub-Plan HITL gate → Human approves

6. GovernanceGuardian checks token budget
   → cumulative_tokens(plan_output + sub_plan_output) < 110K → OK

7. Fan-out: cost_advisor + data_analysis (parallel, max 3)
   → Each agent: budget.allocate() → budget.truncate() → execute()
   → cost_advisor calls Azure AI Foundry LLM
   → data_analysis runs its analysis

8. Aggregate results
   → Plan + Sub-Plan + specialist responses merged
   → Return to user via HTTP response
```

---

## Advanced: Forge-Only Agents (No Python)

If your agent doesn't need custom logic, you can create it entirely in
`forge/` YAML. The `GenericAgent` class handles execution:

1. Create `forge/agents/<id>/agent.yaml` with prompts and skills
2. Register in `forge/_registry.yaml`
3. Add keyword patterns in `router.py`
4. **Skip** creating a Python file — `GenericAgent` is used automatically

This is how `code_research` and `data_analysis` currently work — they have
forge manifests but no dedicated Python class.

---

## Quick Reference

### Files to create/modify when adding an agent

| # | File | Action |
|---|------|--------|
| 1 | `forge/agents/<id>/agent.yaml` | **Create** — manifest (canonical identity) |
| 2 | `forge/agents/<id>/prompts/system.md` | **Create** — system prompt |
| 3 | `forge/agents/<id>/skills/*.yaml` | **Create** — skill definitions |
| 4 | `forge/_registry.yaml` | **Edit** — add agent entry |
| 5 | `src/orchestrator/router.py` | **Edit** — `AgentType` enum + keywords |
| 6 | `src/agents/<id>_agent.py` | **Create** — Python class (optional if GenericAgent suffices) |
| 7 | `src/main.py` | **Edit** — import + `_SPECIALISED_CLASSES` + `_default_agents` |
| 8 | `tests/test_<id>.py` | **Create** — minimum 3 tests |

### Useful commands

```powershell
# Run one test file
.venv\Scripts\python.exe -m pytest tests/test_<id>.py -v

# Full test suite
.venv\Scripts\python.exe -m pytest tests/ -q --tb=short

# Lint
.venv\Scripts\python.exe -m ruff check src/ tests/

# Start server
.venv\Scripts\python.exe -m src.main serve

# Check agent catalog
curl http://localhost:8080/agents
```

### Related documentation

| Document | Section |
|----------|---------|
| [GUIDE.md § 11](GUIDE.md#adding-a-brand-new-agent) | Adding agents — detailed walkthrough |
| [GUIDE.md § 9–10](GUIDE.md#expanding-plan-agent-capabilities) | Expanding existing agents |
| [.github/skills/add-agent/SKILL.md](.github/skills/add-agent/SKILL.md) | Copilot skill for scaffolding agents |
| [.github/skills/budget-calculator/SKILL.md](.github/skills/budget-calculator/SKILL.md) | Token budget verification |
| [SOURCE_OF_TRUTH.md](SOURCE_OF_TRUTH.md) | Canonical ownership map for agent identity |
