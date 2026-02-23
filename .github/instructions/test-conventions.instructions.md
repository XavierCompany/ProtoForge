---
name: 'Test Conventions'
description: 'Rules for ProtoForge pytest tests — fixtures, async patterns, naming, conftest usage'
applyTo: 'tests/**'
---

# Test Conventions

## Framework

- **pytest** + **pytest-asyncio** (mode=auto)
- Fixtures in `tests/conftest.py` — shared across all test files
- One test file per domain: `test_orchestrator.py`, `test_governance.py`, etc.

## Async Tests

All agent and orchestrator tests are async:

```python
async def test_engine_routes_request(engine, mock_agents):
    result = await engine.process("analyze logs")
    assert result["agent_id"] == "log_analysis"
```

No `@pytest.mark.asyncio` decorator needed — `asyncio_mode = "auto"` in pyproject.toml.

## Fixture Pattern

```python
# In conftest.py — use existing fixtures
@pytest.fixture
def guardian(forge_loader):
    return GovernanceGuardian(forge_loader)

# In test files — inject by name
async def test_guardian_enforces_cap(guardian):
    result = await guardian.enforce_hard_cap(tokens=130000)
    assert result.exceeded is True
```

## Naming

- Test files: `test_<domain>.py`
- Test functions: `test_<component>_<behavior>` (e.g., `test_router_classifies_security_intent`)
- Fixtures: noun describing the object (e.g., `engine`, `guardian`, `mock_agents`)

## What NOT to Do

- ❌ Use `unittest.TestCase` — pure pytest functions only
- ❌ Create new conftest.py files in subdirectories
- ❌ Skip `from __future__ import annotations`
- ❌ Use `@pytest.mark.asyncio` — auto mode handles it
- ❌ Mock at too low a level — prefer testing through public APIs
