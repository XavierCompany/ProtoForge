---
name: add-agent
description: Step-by-step procedure for adding a new agent to ProtoForge — creates all 4 identity locations, Python implementation, tests, and updates budget math. Use when user asks to add, create, or scaffold a new agent, specialist, or worker.
metadata:
  author: protoforge
  version: "1.0"
---

# Add a New Agent to ProtoForge

This skill provides the complete, ordered procedure for adding a new agent. Every step is required — skipping any step creates identity drift.

## Prerequisites

- Agent ID chosen (lowercase, underscores, e.g., `perf_monitor`)
- Purpose defined (one sentence)
- Budget allocated (default: 22K = 15K prompt + 7K completion)

## The 4 Identity Locations (ALL Required)

An agent's identity must exist in exactly 4 places. Missing any one causes runtime failures or routing gaps.

| Location | What to Add |
|----------|-------------|
| `forge/agents/<id>/agent.yaml` | Full manifest (id, description, skills, budget) |
| `forge/_registry.yaml` | Agent ID entry |
| `src/orchestrator/router.py` | `AgentType` enum member + keyword patterns |
| `src/agents/<id>_agent.py` | Python class extending `BaseAgent` |

## Step-by-Step Procedure

### 1. Create Forge Directory Structure

```
forge/agents/<agent_id>/
├── agent.yaml
├── instructions/
├── prompts/
└── skills/
```

### 2. Write agent.yaml

```yaml
id: <agent_id>
description: "<purpose>"
skills: []
budget:
  prompt_tokens: 15000
  completion_tokens: 7000
  total: 22000
```

### 3. Register in _registry.yaml

Add the agent ID to the agents list in `forge/_registry.yaml`. Keep alphabetical order.

### 4. Add AgentType Enum Member

In `src/orchestrator/router.py`, add to the `AgentType` enum:

```python
AGENT_ID = "agent_id"
```

Add keyword routing patterns in the `IntentRouter._keyword_patterns` dict.

### 5. Create Python Implementation

Create `src/agents/<agent_id>_agent.py`:

```python
from __future__ import annotations
import structlog
from src.agents.base import BaseAgent

logger = structlog.get_logger(__name__)

class AgentNameAgent(BaseAgent):
    """<purpose>."""

    async def execute(self, request: str, context: dict | None = None) -> dict:
        logger.info("<agent_id>.execute", request_length=len(request))
        # Implementation
        return {
            "agent_id": self.agent_id,
            "response": "...",
            "tokens_used": 0,
        }
```

### 6. Register in main.py

Import and register the agent in `src/main.py`'s agent registration section.

### 7. Create Test File

Create `tests/test_<agent_id>.py` with minimum coverage:

```python
from __future__ import annotations
import pytest
from src.agents.<agent_id>_agent import AgentNameAgent

async def test_basic_execution():
    agent = AgentNameAgent(agent_id="<agent_id>", description="...", skills=[], budget=22000)
    result = await agent.execute("test request")
    assert result["agent_id"] == "<agent_id>"

async def test_manifest_loading(forge_loader):
    manifest = forge_loader.get_agent_manifest("<agent_id>")
    assert manifest["id"] == "<agent_id>"
```

### 8. Verify Budget Math

Calculate: plan(32K) + sub_plan(20K) + top 3 specialist budgets.
Must be ≤ 128K. If the new agent has a budget in the top 3, update:
- `MAINTENANCE.md` §4.3 and §4.4
- `GUIDE2.md` §3
- `README.md` budget section

### 9. Validate

```bash
.venv/Scripts/python.exe -m pytest tests/ -v --tb=short
.venv/Scripts/python.exe -m ruff check src/ tests/
```

### 10. Update Documentation

- Update agent count in README.md, ARCHITECTURE.md, copilot-instructions.md
- Update test count in README.md after new tests pass
- Add CHANGELOG.md `[Unreleased]` entry

## Common Mistakes

| Mistake | Consequence |
|---------|------------|
| Missing `_registry.yaml` entry | ForgeLoader won't find the agent |
| Missing `AgentType` enum | Router can't route to the agent |
| Budget sum > 128K | GovernanceGuardian will block orchestration |
| Forgot `from __future__ import annotations` | Type hint failures on Python 3.12 |
| Synchronous `execute()` | Blocks the event loop |
