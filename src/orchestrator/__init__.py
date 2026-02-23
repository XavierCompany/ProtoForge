"""Orchestrator package — core routing engine and pipeline coordinator.

Modules
-------
engine          OrchestratorEngine — top-level pipeline: route → plan → sub-plan → fan-out → aggregate
router          IntentRouter — keyword patterns + LLM-based intent classification (AgentType enum)
context         ConversationContext (shared state) + AgentResult (structured output)
plan_selector   PlanSelector — Plan Agent HITL gate (prepare_review / resolve)
"""

__all__ = [
    "AgentResult",
    "AgentType",
    "ConversationContext",
    "IntentRouter",
    "OrchestratorEngine",
    "PlanSelector",
    "RoutingDecision",
]
