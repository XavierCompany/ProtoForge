# Routing Rules — When to Dispatch to Which Sub-Agent

## Primary Routing Table

| Signal in User Message | Primary Agent | Common Secondaries |
|------------------------|---------------|-------------------|
| error, log, crash, exception, stack trace, 500 | `log_analysis` | `code_research`, `remediation` |
| code, function, class, implement, source, where is | `code_research` | `knowledge_base` |
| fix, patch, resolve, repair, hotfix, debug | `remediation` | `log_analysis`, `code_research` |
| doc, how to, explain, what is, wiki, knowledge | `knowledge_base` | `code_research` |
| data, metric, chart, trend, statistic, analyze | `data_analysis` | `knowledge_base` |
| security, vulnerability, CVE, scan, audit, threat | `security_sentinel` | `code_research`, `remediation` |

## Escalation Rules

1. **Ambiguous requests** → Default to `knowledge_base` with low confidence
2. **Multi-domain requests** → Pick primary by strongest signal, add secondaries
3. **Meta-requests** (about the system itself) → `knowledge_base`
4. **Emergency/incident** → `log_analysis` primary, fan out to all relevant

## Confidence Thresholds

- **≥ 0.7** — Dispatch directly to identified agents
- **0.4–0.7** — Use the routing prompt with LLM for better classification
- **< 0.4** — Default to `knowledge_base`, flag as uncertain

## Context Passing Rules

- Always pass the Plan Agent's output to sub-agents via `working_memory.plan_output`
- Summarize previous agent results before passing to the next agent
- Never forward raw conversation history exceeding 4000 tokens to a sub-agent
