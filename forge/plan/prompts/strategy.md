Given the user's request and the available sub-agents, build an execution strategy.

## Strategy Template

**Objective:** {{objective}}

**Phase 1 — Discovery**
- What information do we need before acting?
- Which agents gather that information?

**Phase 2 — Analysis**
- What patterns, risks, or dependencies exist?
- Which agents analyze the gathered data?

**Phase 3 — Execution**
- What concrete actions need to happen?
- Which agents execute those actions?
- What is the execution order and parallelism?

**Phase 4 — Validation**
- How do we verify the work is correct?
- What success criteria apply?

## Context Window Guidance

When building the strategy, be mindful of context budgets:
- Keep each sub-agent's input focused and relevant
- Summarize large artifacts before passing downstream
- Prioritize the most recent and relevant context
- Drop stale conversation history when the budget is tight

## Constraints

{{constraints}}
