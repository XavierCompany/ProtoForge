# ProtoForge — Windsurf Rules
#
# Full content variant for Windsurf / Codeium.
# Canonical source: .github/copilot-instructions.md. If they diverge, that file wins.
# The repo-root .windsurfrules file is a thin pointer that redirects here.

# What is ProtoForge?

A plan-first multi-agent orchestrator built on the Microsoft Agent Framework
(Python). Plan Agent coordinates specialists via HITL gates. 128K token cap.

# Key files

src/agents/base.py         — ABC for all agents (execute())
src/orchestrator/engine.py — Core pipeline: route→plan→sub-plan→fan-out→aggregate
src/orchestrator/router.py — IntentRouter (keyword + LLM classification)
src/governance/guardian.py  — Token cap + HITL enforcement
src/forge/loader.py        — Reads forge/ YAML at startup
src/server.py              — FastAPI app factory + route registration
src/server_routes/*.py     — Modular FastAPI route groups (37 endpoints)
forge/agents/*/agent.yaml  — Per-agent manifests (canonical identity)
forge/_context_window.yaml — Token budget config

# Conventions

- Python 3.12+, type hints, from __future__ import annotations
- Pydantic v2 settings, dataclasses for domain, structlog logging
- All execute() are async def
- Token math: plan(32K) + sub-plan(20K) + 3×specialist(≤25K) ≤ 128K
- 485 tests: pytest + pytest-asyncio

# Documentation — Read ALL: copilot-instructions.md → ARCHITECTURE.md →
# SOURCE_OF_TRUTH.md → MAINTENANCE.md → TODO.md → CHANGELOG.md →
# README.md → GUIDE.md → GUIDE2.md → BUILDING_AGENTS.md
