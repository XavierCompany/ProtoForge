# Shared Output Format Standards

All agents MUST return structured output following these rules.

## Envelope
Every agent response is wrapped in a standard envelope:
```yaml
agent_id: "<agent identifier>"
status: success | partial | error
timestamp: "<ISO 8601>"
duration_ms: <execution time>
tokens_used:
  input: <count>
  output: <count>
payload:
  # Agent-specific content here
```

## Formatting Rules
1. **YAML** for structured data (findings, metrics, plans)
2. **Markdown** for narrative explanations
3. **Code blocks** with language tags for code snippets
4. **Tables** for comparisons and summaries
5. **Lists** for sequential steps or enumerated items

## Truncation
When output exceeds the context budget:
1. Summarize — condense findings into key points
2. Prioritize — keep highest-severity / most-relevant items
3. Reference — point to full output location if stored elsewhere
4. Disclose — always note that truncation occurred

## Metadata
Always include:
- `confidence: 0.0-1.0` on analytical conclusions
- `sources: [...]` for knowledge-based answers
- `severity: critical|high|medium|low|info` for findings
