---
name: budget-calculator
description: Recalculate ProtoForge token budgets and verify the 128K context window cap. Use when user changes agent budgets, adds agents, or asks about token allocation, context window math, or budget headroom.
metadata:
  author: protoforge
  version: "1.0"
---

# Token Budget Calculator

This skill helps verify that ProtoForge's 128K context window cap is maintained whenever agent budgets change.

## The Hard Constraint

```
plan + sub_plan + top_3_specialists ≤ 128,000 tokens
```

The orchestrator fans out to at most 3 specialist agents in parallel. The worst case is always: Plan Agent + Sub-Plan Agent + the 3 highest-budget specialists.

## Current Budget Values

Read these from the YAML files — **never use memorized values**:

| Source File | What It Contains |
|-------------|-----------------|
| `forge/_context_window.yaml` | Global cap (128K) and warning threshold (110K) |
| `forge/agents/<id>/agent.yaml` | Per-agent `budget.total` |
| `forge/plan/agent.yaml` | Plan Agent budget |

## Recalculation Procedure

### Step 1: Gather All Budgets

Read every `agent.yaml` and extract `budget.total`:

```bash
grep -r "total:" forge/agents/*/agent.yaml forge/plan/agent.yaml
```

### Step 2: Identify Top 3 Specialists

Sort all specialist budgets (excluding plan and sub_plan) descending. Take the top 3.

Specialists are: code_research, knowledge_base, log_analysis, remediation, security_sentinel, data_analysis, github_tracker, workiq.

### Step 3: Calculate Worst Case

```
worst_case = plan + sub_plan + specialist_1 + specialist_2 + specialist_3
headroom = 128000 - worst_case
```

### Step 4: Verify

- `worst_case ≤ 128000` — PASS
- `headroom ≥ 2000` — Healthy (recommended minimum)
- `headroom < 2000` — Warning: consider reducing budgets

### Step 5: Update Documentation

If budgets changed, update ALL of these:

| File | Section |
|------|---------|
| `forge/agents/<id>/agent.yaml` | `budget` block |
| `forge/_context_window.yaml` | If global cap or threshold changed |
| `MAINTENANCE.md` | §4.3 (per-agent budgets) and §4.4 (worst-case math) |
| `GUIDE2.md` | §3 (budget overview) |
| `README.md` | Budget formula in architecture section |
| `ARCHITECTURE.md` | Budget formula line |
| `copilot-instructions.md` | Token math constraint line |

## Example Calculation

```
Current (Feb 2026):
  plan:            32,000
  sub_plan:        20,000
  code_research:   25,000  ← top 1
  knowledge_base:  25,000  ← top 2
  log_analysis:    22,000  ← top 3
  ─────────────────────────
  Worst case:     124,000
  Headroom:         4,000  ✅
```

## Warning Thresholds

| Headroom | Status | Action |
|----------|--------|--------|
| ≥ 4,000 | Healthy | None needed |
| 2,000–3,999 | Tight | Document the constraint |
| < 2,000 | Critical | Reduce budgets or split orchestration |
| ≤ 0 | **VIOLATION** | GovernanceGuardian will block execution |
