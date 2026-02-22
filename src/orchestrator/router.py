"""Intent classification and routing logic for the orchestrator."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any


class AgentType(str, Enum):
    """All available subagent types."""

    PLAN = "plan"
    LOG_ANALYSIS = "log_analysis"
    CODE_RESEARCH = "code_research"
    REMEDIATION = "remediation"
    KNOWLEDGE_BASE = "knowledge_base"
    DATA_ANALYSIS = "data_analysis"
    SECURITY_SENTINEL = "security_sentinel"


@dataclass
class RoutingDecision:
    """Result of intent classification — which agent(s) to dispatch to."""

    primary_agent: AgentType
    secondary_agents: list[AgentType]
    confidence: float
    reasoning: str
    extracted_params: dict[str, Any]


# Keyword-based fast routing (fallback when LLM routing is unavailable)
KEYWORD_ROUTES: dict[AgentType, list[str]] = {
    AgentType.PLAN: [
        r"\bplan\b", r"\bbreak\s*down\b", r"\bdecompose\b", r"\bstrategy\b",
        r"\bsteps?\b", r"\bapproach\b", r"\barchitect\b",
    ],
    AgentType.LOG_ANALYSIS: [
        r"\blog[s]?\b", r"\berror\s*log\b", r"\bstack\s*trace\b", r"\btraceback\b",
        r"\bcrash\b", r"\bexception\b", r"\b500\b", r"\b4[0-9]{2}\b",
    ],
    AgentType.CODE_RESEARCH: [
        r"\bcode\b", r"\bfunction\b", r"\bclass\b", r"\bimplement", r"\bsearch\s*code\b",
        r"\bfind.*function\b", r"\bwhere\s*is\b", r"\bsource\b",
    ],
    AgentType.REMEDIATION: [
        r"\bfix\b", r"\bremediat", r"\bpatch\b", r"\bresolve\b", r"\brepair\b",
        r"\bhotfix\b", r"\bworkaround\b", r"\bmitigat",
        r"\bfix\s.*\b(?:error|exception|bug|issue|problem)\b",
        r"\bnull\s*pointer\b", r"\bdebug\b",
    ],
    AgentType.KNOWLEDGE_BASE: [
        r"\bdoc[s]?\b", r"\bdocument", r"\bknowledge\b", r"\bwiki\b",
        r"\bhow\s*to\b", r"\bexplain\b", r"\bwhat\s*is\b",
    ],
    AgentType.DATA_ANALYSIS: [
        r"\bdata\b", r"\banalyze\b", r"\banalysis\b", r"\bmetric[s]?\b",
        r"\bchart\b", r"\bgraph\b", r"\btrend\b", r"\bstatistic",
    ],
    AgentType.SECURITY_SENTINEL: [
        r"\bsecurity\b", r"\bvulnerab", r"\bcve\b", r"\bscan\b",
        r"\baudit\b", r"\bpermission\b", r"\baccess\s*control\b", r"\bthreat\b",
    ],
}


class IntentRouter:
    """Routes user messages to the appropriate subagent(s).

    Uses a two-tier approach:
    1. Fast keyword matching for obvious intents
    2. LLM-based classification for ambiguous queries
    """

    def __init__(self) -> None:
        self._compiled_patterns: dict[AgentType, list[re.Pattern[str]]] = {
            agent: [re.compile(p, re.IGNORECASE) for p in patterns]
            for agent, patterns in KEYWORD_ROUTES.items()
        }

    def route_by_keywords(self, message: str) -> RoutingDecision:
        """Fast keyword-based routing (no LLM call needed)."""
        scores: dict[AgentType, int] = {agent: 0 for agent in AgentType}

        for agent, patterns in self._compiled_patterns.items():
            for pattern in patterns:
                if pattern.search(message):
                    scores[agent] += 1

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)

        if ranked[0][1] == 0:
            # No keywords matched — default to knowledge base
            return RoutingDecision(
                primary_agent=AgentType.KNOWLEDGE_BASE,
                secondary_agents=[],
                confidence=0.3,
                reasoning="No keyword matches; defaulting to knowledge base",
                extracted_params={},
            )

        primary = ranked[0]
        total_score = sum(s for _, s in ranked if s > 0)
        confidence = min(primary[1] / max(total_score, 1), 1.0)

        secondary = [agent for agent, score in ranked[1:3] if score > 0]

        return RoutingDecision(
            primary_agent=primary[0],
            secondary_agents=secondary,
            confidence=confidence,
            reasoning=f"Keyword match: {primary[0].value} scored {primary[1]}/{total_score}",
            extracted_params={},
        )

    def get_llm_routing_prompt(self, message: str) -> str:
        """Generate a prompt for LLM-based intent classification."""
        agent_descriptions = {
            AgentType.PLAN: "Task planning, decomposition, strategy, architecture decisions",
            AgentType.LOG_ANALYSIS: "Log parsing, error analysis, stack traces, crash investigation",
            AgentType.CODE_RESEARCH: "Code search, function lookup, implementation understanding",
            AgentType.REMEDIATION: "Bug fixes, patches, hotfixes, workarounds, resolution steps",
            AgentType.KNOWLEDGE_BASE: "Documentation, how-to guides, explanations, knowledge retrieval",
            AgentType.DATA_ANALYSIS: "Data analysis, metrics, charts, trends, statistical analysis",
            AgentType.SECURITY_SENTINEL: "Security scanning, vulnerability assessment, CVE lookup, audits",
        }

        agents_str = "\n".join(
            f"  - {agent.value}: {desc}" for agent, desc in agent_descriptions.items()
        )

        return f"""Classify the user's intent and select the best agent to handle it.

Available agents:
{agents_str}

User message: "{message}"

Respond in JSON format:
{{
  "primary_agent": "<agent_name>",
  "secondary_agents": ["<agent_name>", ...],
  "confidence": 0.0-1.0,
  "reasoning": "<brief explanation>",
  "extracted_params": {{}}
}}"""
