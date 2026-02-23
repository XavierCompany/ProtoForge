"""Agents package — all subagents for the orchestrator.

Exports every concrete agent class.  Two former boiler-plate agents
(``CodeResearchAgent``, ``DataAnalysisAgent``) have been replaced by
:class:`GenericAgent`, whose behaviour is driven entirely by its
forge manifest.
"""

from src.agents.base import BaseAgent
from src.agents.generic import GenericAgent
from src.agents.knowledge_base_agent import KnowledgeBaseAgent
from src.agents.log_analysis_agent import LogAnalysisAgent
from src.agents.plan_agent import PlanAgent
from src.agents.remediation_agent import RemediationAgent
from src.agents.security_sentinel_agent import SecuritySentinelAgent

__all__ = [
    "BaseAgent",
    "GenericAgent",
    "KnowledgeBaseAgent",
    "LogAnalysisAgent",
    "PlanAgent",
    "RemediationAgent",
    "SecuritySentinelAgent",
]
