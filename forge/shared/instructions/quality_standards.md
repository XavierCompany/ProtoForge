# Quality Standards — All Agents

## Response Quality
- **Accurate** — No hallucinated facts; unsure → say so
- **Sourced** — Cite specific files, lines, docs, URLs
- **Actionable** — Every finding includes a recommended next step
- **Concise** — Respect context budgets; summarize long outputs
- **Structured** — Use the standard output envelope format

## Code Quality (when generating or reviewing code)
- Follow the language's idiomatic style
- Include error handling for all external calls
- No hardcoded secrets or credentials
- Add comments only where intent is non-obvious
- Prefer small, testable functions

## Security Baseline
- Never log or output secrets, tokens, or PII
- Sanitize file paths before display (strip home directory)
- Validate all inputs — never trust user-supplied data blindly
- Use parameterized queries for any data-store interaction

## Performance
- Stay within allocated context budget
- Prefer parallel execution when steps are independent
- Cache intermediate results when re-used across steps
