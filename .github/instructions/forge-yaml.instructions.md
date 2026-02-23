---
name: 'Forge YAML Schema Conventions'
description: 'Rules for editing forge/ YAML files — agent manifests, registry, context window budgets'
applyTo: 'forge/**/*.yaml'
---

# Forge YAML Conventions

## File Types

| File | Purpose | Canonical? |
|------|---------|-----------|
| `forge/_registry.yaml` | Agent ID registry — single source of truth | Yes |
| `forge/_context_window.yaml` | Token budget config (128K cap) | Yes |
| `forge/agents/<id>/agent.yaml` | Per-agent manifest | Yes — agent identity |
| `forge/plan/agent.yaml` | Plan Agent manifest | Yes |

## Agent Manifest Schema (agent.yaml)

```yaml
id: agent_name          # Must match directory name and _registry.yaml
description: "..."      # One-line purpose
skills:
  - skill_name_1
  - skill_name_2        # Max 4 skills (governance enforced)
budget:
  prompt_tokens: 17000  # Context budget for prompts
  completion_tokens: 8000  # Budget for completions
  total: 25000          # prompt + completion = total
```

## Budget Math Constraint

```
plan(32K) + sub_plan(20K) + top 3 specialists ≤ 128K
```

Current worst case: 32K + 20K + 25K + 25K + 22K = 124K (4K headroom).

**If you change ANY budget value, recalculate the total and verify ≤ 128K.**

Update these files when budget changes:
1. `forge/_context_window.yaml`
2. `forge/agents/<id>/agent.yaml`
3. `MAINTENANCE.md` §4.3 and §4.4
4. `GUIDE2.md` §3

## What NOT to Do

- ❌ Change an agent `id` without updating `_registry.yaml`, `router.py` AgentType enum, and the Python class
- ❌ Add more than 4 skills per agent without governance review
- ❌ Use budget values that make the sum exceed 128K
