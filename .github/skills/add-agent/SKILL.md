---
name: add-agent
description: Step-by-step procedure for adding a new agent to ProtoForge — creates all 4 identity locations, Python implementation, tests, and updates budget math. Use when user asks to add, create, or scaffold a new agent, specialist, or worker.
metadata:
  author: protoforge
  version: "1.0"
---

# Add a New Agent to ProtoForge

Every step is required — skipping any creates identity drift. Read `SOURCE_OF_TRUTH.md` for the canonical ownership map.

## The 4 Identity Locations (ALL Required)

| # | Location | What to Add |
|---|----------|-------------|
| 1 | `forge/agents/<id>/agent.yaml` | Full manifest (canonical source) |
| 2 | `forge/_registry.yaml` | Agent ID entry |
| 3 | `src/orchestrator/router.py` | `AgentType` enum + keyword patterns |
| 4 | `src/agents/<id>_agent.py` | Python class extending `BaseAgent` |

## Procedure

### 1. Create forge directory

```
forge/agents/<id>/
├── agent.yaml
├── instructions/
├── prompts/
└── skills/
```

Write `agent.yaml` — read an existing one (e.g., `forge/agents/log_analysis/agent.yaml`) as template. Default budget: 22K (15K prompt + 7K completion).

### 2. Register in `forge/_registry.yaml`

Add agent entry under `agents:`. Keep alphabetical order.

### 3. Add `AgentType` enum member in `src/orchestrator/router.py`

Read the existing enum to match the naming pattern. Add keyword routing patterns.

### 4. Create `src/agents/<id>_agent.py`

Read `src/agents/base.py` for the ABC and an existing agent (e.g., `log_analysis_agent.py`) as template. Required pattern:

```python
from __future__ import annotations
import structlog
from src.agents.base import BaseAgent

logger = structlog.get_logger(__name__)

class NameAgent(BaseAgent):
    async def execute(self, request: str, context: dict | None = None) -> dict:
        logger.info("<id>.execute", request_length=len(request))
        return {"agent_id": self.agent_id, "response": "...", "tokens_used": 0}
```

### 5. Register in `src/main.py`

Add import and registration in the agent registration section.

### 6. Create `tests/test_<id>.py`

Read `tests/conftest.py` for available fixtures. Minimum tests: basic execution, manifest loading, intent routing.

### 7. Verify budget math

Read `forge/_context_window.yaml` for the hard cap. Read ALL `agent.yaml` files for budget totals. Calculate: plan + sub_plan + top 3 specialists ≤ hard cap. Update docs per `MAINTENANCE.md` if budget math changed.

### 8. Validate

```bash
.venv/Scripts/python.exe -m pytest tests/ -v --tb=short
.venv/Scripts/python.exe -m ruff check src/ tests/
```

Update agent count in README.md, ARCHITECTURE.md, copilot-instructions.md. Update test count after new tests pass.
