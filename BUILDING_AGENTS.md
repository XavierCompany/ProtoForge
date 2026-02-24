# Building Agents with ProtoForge

> Practical guide: add a new specialist agent in 8 steps.
> Uses a "Cost Advisor" agent as the worked example.
>
> This is doc **10 of 10** in the reading order.
> Prerequisite: [ARCHITECTURE.md](ARCHITECTURE.md) for system overview.

---

## How the Pipeline Works

```
User → /chat → IntentRouter (keywords)
  → Plan Agent (strategy + HITL gate)
    → Sub-Plan Agent (resources + HITL gate)
      → Fan-out to ≤3 specialists in parallel
        → Aggregate → User
```

You never call a specialist directly. The Plan Agent decides which to invoke.
Your job: **declare** what the agent does, **route** when to suggest it,
**implement** how it works.

---

## 8-Step Tutorial: Build a "Cost Advisor" Agent

### Step 1 — Forge Manifest

Create `forge/agents/cost_advisor/agent.yaml`:

```yaml
id: cost_advisor
name: Cost Advisor Agent
type: specialist
description: >
  Analyses cloud spending and recommends cost optimisations.
version: "1.0.0"
context_budget:
  max_input_tokens: 15000
  max_output_tokens: 7000
  strategy: priority
subagents: []
prompts:
  system: system.md
skills:
  - analyze_costs.yaml
instructions:
  - cost_guidelines.md
tags: [cost, optimization, billing, cloud, infrastructure]
```

Create `forge/agents/cost_advisor/prompts/system.md`:

```markdown
You are the Cost Advisor Agent — an expert in cloud cost optimisation.

Responsibilities:
1. Analyse billing data and resource utilisation
2. Identify over-provisioned or idle resources
3. Recommend right-sizing, reserved instances, spot usage
4. Estimate savings per recommendation

Output: Executive summary → Ranked recommendations (HIGH/MEDIUM/LOW) → Timeline.
```

### Step 2 — Register in `forge/_registry.yaml`

```yaml
agents:
  cost_advisor:
    path: agents/cost_advisor/
    type: specialist
    description: "Analyses cloud spending and recommends cost optimisations"
```

### Step 3 — Add Routing Patterns

In `src/orchestrator/router.py`:

```python
# AgentType enum:
COST_ADVISOR = "cost_advisor"

# _BUILTIN_KEYWORD_ROUTES:
AgentType.COST_ADVISOR: [
    r"\bcost[s]?\b", r"\bbilling\b", r"\bspend\b",
    r"\boptimi[sz]e\b", r"\bbudget\b", r"\bpricing\b",
],
```

### Step 4 — Python Agent (optional)

If `GenericAgent` suffices (no custom logic), **skip this step** — the loader
uses `GenericAgent` automatically. This is how `code_research` and
`data_analysis` work today.

For custom logic, create `src/agents/cost_advisor_agent.py`:

```python
"""Cost Advisor Agent — cloud spending analysis."""
from __future__ import annotations
from typing import Any
import structlog
from src.agents.base import BaseAgent
from src.orchestrator.context import AgentResult, ConversationContext

logger = structlog.get_logger(__name__)

class CostAdvisorAgent(BaseAgent):
    async def execute(
        self, message: str, context: ConversationContext,
        params: dict[str, Any] | None = None,
    ) -> AgentResult:
        messages = self._build_messages(message, context)
        # Wire LLM call here (see Wiring LLM Calls section below)
        response = f"**Cost Advisor Report**\nAnalysis of: {message[:100]}..."
        return AgentResult(
            agent_id=self.agent_id, content=response,
            confidence=0.5, artifacts={"analysis_type": "cost_optimisation"},
        )
```

### Step 5 — Register in Bootstrap

In `src/main.py`, add to `_SPECIALISED_CLASSES` and `_default_agents`:

```python
from src.agents.cost_advisor_agent import CostAdvisorAgent

_SPECIALISED_CLASSES["cost_advisor"] = CostAdvisorAgent

# In _default_agents:
AgentType.COST_ADVISOR: (CostAdvisorAgent, "Cost Advisor Agent", "Cloud cost analysis"),
```

### Step 6 — Write Tests

```python
# tests/test_cost_advisor.py
import pytest
from src.agents.cost_advisor_agent import CostAdvisorAgent
from src.orchestrator.context import ConversationContext

@pytest.fixture
def agent():
    return CostAdvisorAgent()

@pytest.mark.asyncio
async def test_execute(agent):
    result = await agent.execute("Analyze costs", ConversationContext())
    assert result.agent_id == "cost_advisor"
    assert result.content
    assert result.confidence > 0
```

### Step 7 — Verify Budget Math

Context window constraint: `plan(32K) + sub_plan(20K) + 3 specialists ≤ 128K`

Your agent = 22K (15K + 7K). Worst case with top 3:

```
32K + 20K + 25K + 25K + 22K = 124K  ✅ (4K headroom)
```

If your budget changes, recalculate. See the
[budget-calculator skill](.github/skills/budget-calculator/SKILL.md).

### Step 8 — Run and Validate

```powershell
.venv\Scripts\python.exe -m pytest tests/test_cost_advisor.py -v --tb=short
.venv\Scripts\python.exe -m pytest tests/ -q --tb=short
.venv\Scripts\python.exe -m ruff check src/ tests/
```

---

## Wiring LLM Calls (Azure AI Foundry)

Set environment variables:

```env
DEFAULT_LLM_PROVIDER=azure_ai_foundry
AZURE_AI_FOUNDRY_ENDPOINT=https://your-resource.services.ai.azure.com
AZURE_AI_FOUNDRY_MODEL=gpt-5.2-chat
```

Integration pattern for your agent's `execute()`:

```python
from azure.ai.inference.aio import ChatCompletionsClient
from azure.identity.aio import DefaultAzureCredential
from src.config import get_settings

async def execute(self, message, context, params=None):
    messages = self._build_messages(message, context)
    settings = get_settings()
    credential = DefaultAzureCredential()
    async with ChatCompletionsClient(
        endpoint=settings.llm.azure_endpoint, credential=credential,
    ) as client:
        response = await client.complete(
            model=settings.llm.azure_model, messages=messages,
        )
    return AgentResult(
        agent_id=self.agent_id,
        content=response.choices[0].message.content,
        confidence=0.8,
    )
```

Provider auto-detection priority (when `DEFAULT_LLM_PROVIDER` not set):

| Priority | Provider | Credential |
|----------|----------|------------|
| 1 | Anthropic (Claude Opus 4.6) | `ANTHROPIC_API_KEY` |
| 2 | Azure AI Foundry | `AZURE_AI_FOUNDRY_ENDPOINT` |
| 3 | OpenAI (Codex 5.3) | `OPENAI_API_KEY` |
| 4 | Google (Gemini Pro 3.1) | `GOOGLE_API_KEY` |

---

## Forge-Only Agents (No Python)

If your agent needs no custom logic:

1. Create `forge/agents/<id>/agent.yaml` with prompts and skills
2. Register in `forge/_registry.yaml`
3. Add keyword patterns in `router.py`

`GenericAgent` handles execution automatically. This is how `code_research`
and `data_analysis` work.

---

## Quick Reference

| # | File | Action |
|---|------|--------|
| 1 | `forge/agents/<id>/agent.yaml` | **Create** — manifest |
| 2 | `forge/agents/<id>/prompts/system.md` | **Create** — system prompt |
| 3 | `forge/_registry.yaml` | **Edit** — add entry |
| 4 | `src/orchestrator/router.py` | **Edit** — enum + keywords |
| 5 | `src/agents/<id>_agent.py` | **Create** — optional if GenericAgent works |
| 6 | `src/main.py` | **Edit** — `_SPECIALISED_CLASSES` (only if Step 5) |
| 7 | `tests/test_<id>.py` | **Create** — minimum 3 tests |

**Related docs:**
[GUIDE.md § 11](GUIDE.md#adding-a-brand-new-agent) (detailed walkthrough) |
[GUIDE2.md § 4](GUIDE2.md#4-adding-a-new-subagent) (maintenance perspective) |
[add-agent skill](.github/skills/add-agent/SKILL.md) (Copilot scaffolding)
