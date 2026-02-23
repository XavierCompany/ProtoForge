Given the user's message, identify which sub-agents should be invoked.

## Message

{{message}}

## Available Sub-Agents

{{agent_descriptions}}

## Routing Rules

1. Choose the PRIMARY agent — the single best match for the core intent.
2. Choose SECONDARY agents — additional agents that add value (0-3 max).
3. Rate your confidence: 0.0 (guessing) to 1.0 (certain).
4. Explain your reasoning in one sentence.

## Response Format

```json
{
  "primary_agent": "<agent_name>",
  "secondary_agents": ["<agent_name>", ...],
  "confidence": 0.85,
  "reasoning": "<why these agents>"
}
```

## Heuristics

- If the message mentions errors, logs, crashes → `log_analysis`
- If the message asks about code, functions, implementations → `code_research`
- If the message asks to fix, patch, resolve → `remediation`
- If the message asks to explain, document, how-to → `knowledge_base`
- If the message mentions data, metrics, trends → `data_analysis`
- If the message mentions security, vulnerabilities, CVE → `security_sentinel`
- When uncertain, default to `knowledge_base`
