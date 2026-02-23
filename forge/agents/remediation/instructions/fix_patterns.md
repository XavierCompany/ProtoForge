# Fix Patterns — Common Remediation Strategies

## Pattern: Null/Undefined Reference
- **Symptom:** NullPointerException, TypeError, AttributeError
- **Fix:** Add null checks, optional chaining, default values
- **Risk:** LOW — purely additive

## Pattern: Race Condition
- **Symptom:** Intermittent failures, data corruption
- **Fix:** Add locks, use atomic operations, serialize access
- **Risk:** MEDIUM — may impact performance

## Pattern: Resource Leak
- **Symptom:** Memory growth, connection pool exhaustion
- **Fix:** Add proper cleanup (finally, using, context manager)
- **Risk:** LOW — purely additive

## Pattern: SQL Injection
- **Symptom:** Security scan finding, unusual query behavior
- **Fix:** Parameterized queries, ORM usage, input validation
- **Risk:** LOW — security improvement

## Pattern: Timeout/Deadlock
- **Symptom:** Hanging requests, timeout errors
- **Fix:** Add timeouts, circuit breakers, retry with backoff
- **Risk:** MEDIUM — may change failure behavior

## Fix Quality Checklist
- [ ] Fix addresses the root cause, not just the symptom
- [ ] Fix is minimal (smallest change that resolves the issue)
- [ ] Fix includes error handling for edge cases
- [ ] Fix doesn't break existing tests
- [ ] Rollback plan is documented
