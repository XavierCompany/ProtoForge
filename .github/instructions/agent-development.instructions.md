---
name: 'Agent Development Conventions'
description: 'Rules for implementing ProtoForge agents — BaseAgent ABC, async execute(), from_manifest pattern, structlog'
applyTo: 'src/agents/**'
---

# Agent Development Conventions

## BaseAgent ABC

Every agent extends `BaseAgent` from `src/agents/base.py`. Two construction paths:

```python
# Path 1: from_manifest (preferred for forge-registered agents)
agent = MyAgent.from_manifest(manifest_dict, context_budget_manager)

# Path 2: explicit (for tests or ad-hoc)
agent = MyAgent(agent_id="my_agent", description="...", skills=[], budget=22000)
```

## Required Pattern

```python
from __future__ import annotations
import structlog
from src.agents.base import BaseAgent

logger = structlog.get_logger(__name__)

class MyAgent(BaseAgent):
    """One-line description of what this agent does."""

    async def execute(self, request: str, context: dict | None = None) -> dict:
        logger.info("my_agent.execute", request_length=len(request))
        # Implementation here
        return {"agent_id": self.agent_id, "response": result, "tokens_used": count}
```

## Rules

- `execute()` is always `async def` — never synchronous
- Return dict must include `agent_id`, `response`, and `tokens_used`
- Use `structlog.get_logger(__name__)` — never `print()` or `logging`
- Type hints on every function signature
- `from __future__ import annotations` at top of every file
- Budget enforcement: call `self.check_budget(tokens)` before expensive operations
- GenericAgent handles agent types without dedicated files (code_research, data_analysis)

## What NOT to Do

- ❌ Override `__init__` without calling `super().__init__(**kwargs)`
- ❌ Import from other agent files — agents are independent
- ❌ Block the event loop with synchronous I/O
- ❌ Exceed the agent's token budget from `forge/agents/<id>/agent.yaml`
