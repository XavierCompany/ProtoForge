# Shared Error Handling Prompt

When encountering an error during execution, follow this protocol:

## Error Classification
| Level | Action |
|-------|--------|
| **Recoverable** | Log warning, apply fallback, continue execution |
| **Degraded** | Log error, return partial results with caveat, flag for review |
| **Fatal** | Log critical, abort this agent's work, return error summary to Plan Agent |

## Error Response Format
```yaml
error:
  level: recoverable | degraded | fatal
  code: "<error code or type>"
  message: "<human-readable description>"
  agent: "<agent that encountered the error>"
  context: "<what was being attempted>"
  fallback_applied: true | false
  partial_results: "<any results obtained before failure>"
```

## Retry Policy
- Transient errors (timeout, rate limit): retry up to 2× with exponential backoff
- Data errors (parse failure, schema mismatch): attempt alternative parsing, then degrade
- Auth errors: abort immediately, surface to operator

## Escalation Path
1. Agent handles internally → 2. Return to Plan Agent → 3. Plan Agent re-routes or aborts
