Decompose the following complex request into ordered, actionable steps.

## Request

{{request}}

## Decomposition Rules

1. Each step must be a concrete, verifiable action — not a vague statement.
2. Assign each step to exactly one sub-agent.
3. Mark dependencies between steps (which steps must complete before others).
4. Estimate effort per step: `trivial` | `small` | `medium` | `large`.
5. Identify steps that can run in parallel vs. sequential.

## Output Format

```yaml
steps:
  - id: 1
    action: "<what to do>"
    agent: "<sub-agent name>"
    depends_on: []
    effort: small
    parallel_group: A    # Steps in the same group run in parallel
  - id: 2
    action: "<what to do>"
    agent: "<sub-agent name>"
    depends_on: [1]
    effort: medium
    parallel_group: B
```

## Constraints

- Maximum {{max_steps}} steps (default: 10)
- If the request is simple, use 1-3 steps
- If the request is ambiguous, state assumptions explicitly
