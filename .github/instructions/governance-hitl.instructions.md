---
name: 'Governance & HITL Patterns'
description: 'Rules for editing governance guardian, selector, and HITL review flows'
applyTo: 'src/governance/**'
---

# Governance & HITL Conventions

## HITL Pattern (all 5 gate types)

```
prepare_review() → expose via HTTP endpoint → wait_with_timeout(120s) → resolve()
```

### Timeout Semantics (CRITICAL)

| Gate Type | On Timeout | Rationale |
|-----------|-----------|-----------|
| Plan HITL | **Fail-open** (auto-approve) | Don't block user workflow |
| Sub-Plan HITL | **Fail-open** (auto-approve) | Same |
| Context HITL | **Fail-open** (proceed) | Warn but don't block |
| Skill Cap HITL | **Fail-open** (proceed) | Warn but don't block |
| Lifecycle HITL | **Fail-closed** (reject) | Destructive action — require explicit approval |

**Never change fail-closed to fail-open for lifecycle operations.**

## GovernanceGuardian

- Enforces 128K token hard cap via `enforce_hard_cap()`
- Triggers context HITL at 110K tokens (warning threshold)
- Triggers skill cap HITL when agent has >4 skills
- Never disable `enforce_hard_cap` — it's the safety net

## GovernanceSelector

- Manages agent lifecycle: disable, enable, unregister
- All destructive operations require HITL approval
- Timeout = 120 seconds default

## HTTP Exposure

HITL reviews are exposed as FastAPI endpoints in `src/server.py`:
- `GET /governance/reviews/pending` — list pending reviews
- `POST /governance/reviews/{id}/resolve` — approve/reject

## What NOT to Do

- ❌ Change timeout from 120s without updating docs
- ❌ Remove HITL gate from any lifecycle operation
- ❌ Allow agent unregister without HITL review
- ❌ Use synchronous waits — always `asyncio.wait_for()`
