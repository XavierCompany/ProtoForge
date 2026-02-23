# Coordination Rules — How the Plan Agent Orchestrates Sub-Agents

## Execution Flow

```
User Message
    │
    ▼
Plan Agent (ALWAYS FIRST)
    │
    ├─ Analyzes request
    ├─ Produces execution plan
    ├─ Identifies sub-agents
    │
    ▼
Fan-Out to Sub-Agents (parallel where possible)
    │
    ├─ Sub-Agent A ──────┐
    ├─ Sub-Agent B ──────┤── Parallel Group
    ├─ Sub-Agent C ──────┘
    │
    ▼
Aggregation
    │
    ├─ Plan output (always first in response)
    ├─ Sub-agent outputs (ordered by confidence)
    │
    ▼
Unified Response
```

## Coordination Principles

1. **Plan First** — Never dispatch sub-agents without a plan.
2. **Parallel by Default** — Sub-agents run in parallel unless they have explicit dependencies.
3. **Fail Gracefully** — If a sub-agent fails, include its error but don't abort the pipeline.
4. **Summarize, Don't Dump** — Each sub-agent receives a focused context slice, not the entire history.
5. **Audit Trail** — Every dispatch decision is logged with reasoning.

## Sub-Agent Context Contract

When dispatching to a sub-agent, the Plan Agent provides:

```yaml
context:
  plan_summary: "<1-2 sentence plan summary>"
  your_role: "<what this sub-agent should focus on>"
  prior_findings: "<summary of other agents' results, if any>"
  constraints: "<time, scope, or quality constraints>"
```

## Result Aggregation Rules

1. Plan Agent output is always the **first section** of the response.
2. Sub-agent outputs are appended in **confidence-descending** order.
3. Outputs with confidence < 0.3 are **dropped** (not shown to user).
4. If all sub-agents fail, return the Plan Agent's output alone with a note.
