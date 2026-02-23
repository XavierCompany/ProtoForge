# ProtoForge — Plan-first multi-agent orchestrator (Python, FastAPI, 128K token cap)

Read `.github/copilot-instructions.md` first, then ALL docs listed in its
"Documentation Reading Order" section. Full Claude variant: `llm-instructions/claude.md`.

Key entry points: `src/orchestrator/engine.py` (pipeline), `src/agents/base.py` (ABC),
`src/server.py` (FastAPI app), `forge/agents/*/agent.yaml` (agent manifests).
