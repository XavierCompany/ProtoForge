---
agent: 'agent'
description: 'Add a new FastAPI endpoint to ProtoForge server.py with proper patterns'
model: 'Claude Opus 4.6'
tools: ['editFiles', 'readFile', 'search', 'runInTerminal']
argument-hint: 'HTTP method, path, and purpose (e.g., "GET /agents/{id}/metrics — return agent performance metrics")'
---

# Add FastAPI Endpoint

Add a new HTTP endpoint to `src/server.py` following ProtoForge conventions.

## Conventions

Read `src/server.py` to understand the existing endpoint patterns:

- All endpoints use `@app.get()`, `@app.post()`, etc.
- JSON responses with consistent structure: `{"status": "...", "data": ...}`
- Error responses: `{"error": "...", "detail": "..."}`
- Use `structlog.get_logger(__name__)` for request logging
- Group endpoints by domain (agents, governance, forge, mcp, workiq)
- Add the endpoint near related endpoints

## After Adding

1. Update the endpoint count in README.md (currently 35+)
2. If it's a governance endpoint, add it to GUIDE.md §6
3. Run tests: `.venv/Scripts/python.exe -m pytest tests/ -v --tb=short`
4. Verify server starts: `.venv/Scripts/python.exe -m src.main serve`
