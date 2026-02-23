---
agent: 'agent'
description: 'Run a documentation accuracy audit — verifies numbers, cross-references, and budget math against source code'
model: 'Claude Opus 4.6'
tools: ['readFile', 'search', 'runInTerminal', 'editFiles']
---

# Documentation Accuracy Audit

Audit ProtoForge documentation for accuracy against the live codebase.

## Step 1: Gather Ground Truth

Run terminal commands to collect actual values — never trust numbers in docs:

```bash
# Test count
.venv/Scripts/python.exe -m pytest tests/ --collect-only -q 2>/dev/null | tail -1
# Source files
find src -name "*.py" | wc -l
# Endpoints
grep -cE "@app\.(get|post|put|delete|patch)" src/server.py
# Agent YAML budgets (for budget math verification)
grep -r "total:" forge/agents/*/agent.yaml forge/plan/agent.yaml
# Global cap
grep "hard_cap:" forge/_context_window.yaml
# Version
grep "^version" pyproject.toml
```

## Step 2: Audit Each Documentation File

Read every `.md` file listed in the reading order section of `.github/copilot-instructions.md`.
Also audit `llm-instructions/*.md` variants.

For each file check:
- **Numbers** match ground truth (test count, file count, endpoint count, line counts)
- **Budget math** matches actual YAML values
- **Version strings** match pyproject.toml
- **Cross-references** point to files/functions that exist
- **Reading order** is consistent across all files that list it

## Step 3: Fix and Validate

Fix all stale values. Run tests after all fixes.
Add `[Unreleased]` entry in CHANGELOG.md if any doc was changed.
