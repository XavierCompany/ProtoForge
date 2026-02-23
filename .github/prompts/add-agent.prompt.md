---
agent: 'agent'
description: 'Scaffold a new ProtoForge agent — creates agent.yaml, Python class, test file, and updates registry'
model: 'Claude Opus 4.6'
tools: ['editFiles', 'createFile', 'runInTerminal', 'readFile', 'search']
argument-hint: 'agent name and purpose (e.g., "perf_monitor — monitors performance metrics")'
---

# Add New Agent to ProtoForge

Create a complete new agent following the established pattern. For each new agent:

## Step 1: Choose Agent ID

The ID must be lowercase, underscore-separated, and match across all 4 identity locations.
Read `forge/_registry.yaml` to see existing IDs and pick a unique one.

## Step 2: Create Forge Manifest

Create `forge/agents/${input:agent_id}/agent.yaml`:

```yaml
id: ${input:agent_id}
description: "${input:description}"
skills: []
budget:
  prompt_tokens: 15000
  completion_tokens: 7000
  total: 22000
```

Also create empty subdirectories: `instructions/`, `prompts/`, `skills/`.

## Step 3: Register in _registry.yaml

Add the new agent ID to `forge/_registry.yaml`.

## Step 4: Add AgentType Enum Member

In `src/orchestrator/router.py`, add a new member to the `AgentType` enum matching the agent ID.
Add keyword patterns for intent routing.

## Step 5: Create Python Implementation

Create `src/agents/${input:agent_id}_agent.py` following the BaseAgent pattern.
Read `src/agents/base.py` and an existing agent (e.g., `log_analysis_agent.py`) as templates.
Follow all conventions in `.github/instructions/agent-development.instructions.md`.

## Step 6: Register in main.py

Add the agent import and registration in `src/main.py`.

## Step 7: Create Tests

Create `tests/test_${input:agent_id}.py` with at least:
- Test basic execution
- Test budget enforcement
- Test manifest loading
- Test intent routing to this agent

Follow conventions in `.github/instructions/test-conventions.instructions.md`.

## Step 8: Verify Budget Math

Recalculate: plan(32K) + sub_plan(20K) + top 3 specialists ≤ 128K.
If the new agent's budget is in the top 3, update MAINTENANCE.md §4.4.

## Step 9: Run Validation

```bash
.venv/Scripts/python.exe -m pytest tests/ -v --tb=short
.venv/Scripts/python.exe -m ruff check src/ tests/
```
