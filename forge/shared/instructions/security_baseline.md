# Security Baseline — All Agents

## Mandatory Checks
Every agent MUST enforce these baseline security rules regardless of task type.

### Data Handling
- **Never echo secrets** — mask API keys, tokens, passwords in output
- **Sanitize paths** — remove absolute paths, home dirs, user names
- **Redact PII** — email addresses, phone numbers, SSNs if encountered
- **Scope access** — only access files/data explicitly mentioned in the task

### Input Validation
- Reject inputs > 100 KB unless the agent is specifically designed for large data
- Validate file paths — no directory traversal (../)
- Validate URLs — only HTTPS, no internal/private IPs unless authorized

### Output Safety
- No executable code in output unless explicitly requested
- Wrap code fixes in diff/patch format by default
- Include risk assessment for any code changes

### Audit Requirements
- Log every agent invocation (agent_id, timestamp, task summary)
- Log every finding severity ≥ HIGH
- Preserve chain of custody: which agent said what, when
