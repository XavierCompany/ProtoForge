# Knowledge Sources Configuration

## Source Priority (highest → lowest)
| Priority | Source Type | Description |
|----------|-----------|-------------|
| 1 | Internal docs | Company wikis, Confluence, Notion pages |
| 2 | README / GUIDE | Repository documentation (README.md, GUIDE.md) |
| 3 | API docs | OpenAPI specs, SDK reference docs |
| 4 | Runbooks | Operational runbooks and playbooks |
| 5 | Slack/Teams | Archived discussions (search only) |
| 6 | External docs | Official vendor documentation |
| 7 | Community | Stack Overflow, GitHub Issues, forums |

## Search Strategy
1. **Exact match** — look for the exact phrase first
2. **Keyword expansion** — break into keywords, add synonyms
3. **Semantic search** — use embeddings for conceptual similarity
4. **Recency filter** — prefer sources updated within 6 months

## Freshness Rules
- Sources > 6 months: add ⚠️ freshness warning
- Sources > 12 months: add ❌ staleness warning
- Always include the `last_updated` date when available

## Citation Format
```
[Source Title](url) — last updated YYYY-MM-DD
```
