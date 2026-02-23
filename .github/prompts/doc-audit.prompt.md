---
agent: 'agent'
description: 'Run a comprehensive documentation accuracy audit across all 9 ProtoForge docs'
model: 'Claude Opus 4.6'
tools: ['readFile', 'search', 'runInTerminal', 'editFiles']
---

# Documentation Accuracy Audit

Audit all 9 ProtoForge documentation files for accuracy against the actual source code.

## Audit Targets

1. `.github/copilot-instructions.md`
2. `ARCHITECTURE.md`
3. `SOURCE_OF_TRUTH.md`
4. `MAINTENANCE.md`
5. `TODO.md`
6. `CHANGELOG.md`
7. `README.md`
8. `GUIDE.md`
9. `GUIDE2.md`

## Step 1: Gather Ground Truth

Run these terminal commands to get actual values:

```bash
# Source line counts
find src -name "*.py" | xargs wc -l | sort -rn | head -20
# Total source files
find src -name "*.py" | wc -l
# Test counts per file
.venv/Scripts/python.exe -m pytest tests/ --collect-only -q 2>/dev/null | tail -1
# Endpoint count
grep -c "(@app\.\(get\|post\|put\|delete\|patch\))" src/server.py
# Agent YAML budgets
grep -r "total:" forge/agents/*/agent.yaml
```

## Step 2: Check Each Doc For

- **Stale numbers**: Line counts, test counts, endpoint counts, file counts
- **Phantom references**: Files, functions, or classes mentioned but don't exist
- **Budget math**: All budget formulas must equal actual YAML values
- **Reading order**: Must be consistent across copilot-instructions.md, ARCHITECTURE.md, and llm-instructions/*.md
- **Version strings**: Must all say the same version as pyproject.toml
- **Contradictions**: Same fact stated differently in different docs

## Step 3: Fix and Validate

For each finding:
1. Verify the actual value from source code
2. Fix all docs that reference the stale value
3. Run tests after all fixes: `.venv/Scripts/python.exe -m pytest tests/ -v`
4. Update CHANGELOG.md with an `[Unreleased]` entry
