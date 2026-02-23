"""ProtoForge — Multi-Agent Orchestrator with MCP Skills Distribution.

Packages
--------
agents        All subagents (BaseAgent ABC + 9 specialist implementations)
forge         Declarative YAML ecosystem loader, token budget manager, contributions
governance    Context window guardian + agent lifecycle HITL selector
mcp           Model Context Protocol server (tools, resources, prompts)
orchestrator  Core pipeline engine, intent router, conversation context, plan HITL
registry      Agent catalog, workflow bundling and execution
workiq        Microsoft 365 context integration (WorkIQ client + 2-phase HITL)

Entry Points
------------
main.py       CLI bootstrap — registers agents, starts server
server.py     FastAPI HTTP app — 35+ endpoints + HTML inspector dashboard
config.py     Pydantic Settings (LLM, Server, MCP, Forge, Observability)
"""

__all__ = [
    "agents",
    "forge",
    "governance",
    "mcp",
    "orchestrator",
    "registry",
    "workiq",
]
