# ProtoForge — Cursor Rules
#
# Full content variant for Cursor IDE.
# Canonical source: .github/copilot-instructions.md
# If they diverge, copilot-instructions.md wins.
#
# The repo-root .cursorrules file is a thin pointer that redirects here.
# This file lives in llm-instructions/ for organization.

# What is ProtoForge?

A plan-first multi-agent orchestrator built on the Microsoft Agent Framework
(Python). Every user request flows through a Plan Agent before any specialist
executes. Human-in-the-Loop (HITL) gates at every decision point. Context window
governance enforces a 128 K token hard cap per orchestration run.

# Data Flow

User → FastAPI /chat → IntentRouter (keyword + LLM)
  → Plan Agent (HITL review)
    → Sub-Plan Agent (HITL review)
      → Fan-out to ≤3 specialist agents in parallel
        → Aggregate response → User

# Key source files

src/config.py            — Pydantic Settings
src/main.py              — CLI entry, bootstrap
src/server.py            — FastAPI HTTP app, 35+ endpoints
src/agents/base.py       — ABC for all agents
src/orchestrator/engine.py — Core pipeline
src/orchestrator/router.py — IntentRouter
src/governance/guardian.py — Token cap + HITL enforcement
src/forge/loader.py      — Reads forge/ YAML at startup
forge/_registry.yaml     — Agent registry (source of truth for IDs)
forge/_context_window.yaml — Token budget config (128K cap)
forge/agents/*/agent.yaml — Per-agent manifests

# Coding Conventions

- Python 3.12+, type hints everywhere, from __future__ import annotations
- Pydantic v2 for settings, dataclasses for domain models
- structlog for logging
- All agent execute() methods are async def
- Token math: plan(32K) + sub-plan(20K) + 3×specialist(≤25K) ≤ 128K
- Tests: pytest + pytest-asyncio, 378 tests

# Documentation — Read ALL

1. .github/copilot-instructions.md — orientation (~140 lines)
2. ARCHITECTURE.md — compact architecture, APIs (~255 lines)
3. SOURCE_OF_TRUTH.md — canonical ownership map (~195 lines)
4. MAINTENANCE.md — update protocol (~455 lines)
5. TODO.md — prioritised backlog (~240 lines)
6. CHANGELOG.md — version history (~140 lines)
7. README.md — onboarding, endpoints (~810 lines)
8. GUIDE.md — deep-dive reference, 19 sections (~2760 lines)
9. GUIDE2.md — maintenance & tuning guide (~905 lines)
