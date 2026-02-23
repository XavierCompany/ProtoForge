---
name: budget-calculator
description: Recalculate ProtoForge token budgets and verify the context window cap. Use when user changes agent budgets, adds agents, or asks about token allocation, context window math, or budget headroom.
metadata:
  author: protoforge
  version: "1.0"
---

# Token Budget Calculator

Verify that ProtoForge's context window cap is maintained. **Never use memorized values** — always read from YAML files.

## The Hard Constraint

```
plan + sub_plan + top_3_specialists ≤ hard_cap
```

The orchestrator fans out to at most `max_parallel_agents` specialists (read from `forge/_context_window.yaml` scaling section). The worst case is: Plan Agent + Sub-Plan Agent + the N highest-budget specialists.

## Procedure

### 1. Read the cap

```bash
grep "hard_cap:" forge/_context_window.yaml
grep "max_parallel_agents:" forge/_context_window.yaml
```

### 2. Read all budgets

```bash
grep -r "total:" forge/agents/*/agent.yaml forge/plan/agent.yaml
```

### 3. Calculate worst case

- Identify the plan and sub_plan budgets
- Sort remaining specialist budgets descending
- Take the top N (where N = `max_parallel_agents`)
- Sum: plan + sub_plan + top N specialists
- Headroom = hard_cap - worst_case

### 4. Evaluate

- headroom ≥ 4K → Healthy
- headroom 2K–4K → Tight, document the constraint
- headroom < 2K → Critical, reduce budgets
- headroom ≤ 0 → **VIOLATION** — GovernanceGuardian blocks execution

### 5. Update documentation

If budgets changed, check MAINTENANCE.md for the list of files that must be updated (§4.3 and §4.4). The canonical source for which files to update is `SOURCE_OF_TRUTH.md`.
