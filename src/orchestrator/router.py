"""Intent classification and routing logic for the orchestrator.

The router maps incoming user messages to agent IDs (plain ``str`` values).
The legacy :class:`AgentType` enum is kept for convenience and backward-
compat — its members are valid ``str`` values thanks to :class:`~enum.StrEnum`.

New agents can register routing patterns at runtime via
:meth:`IntentRouter.register_patterns`, so adding a forge-contributed agent
no longer requires editing this file.

**Enriched routing:** When WorkIQ organisational context is available, the
router can combine the user message with selected WorkIQ content to produce
a richer routing decision.  :meth:`extract_routing_keywords` surfaces which
agent keywords appear in the WorkIQ text, and
:meth:`route_with_context` merges both signals.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class AgentType(StrEnum):
    """Well-known agent IDs shipped with ProtoForge.

    Kept as a convenience — the router and engine operate on plain ``str``
    IDs so that forge-contributed agents work without touching this enum.
    """

    PLAN = "plan"
    SUB_PLAN = "sub_plan"
    LOG_ANALYSIS = "log_analysis"
    CODE_RESEARCH = "code_research"
    REMEDIATION = "remediation"
    KNOWLEDGE_BASE = "knowledge_base"
    DATA_ANALYSIS = "data_analysis"
    SECURITY_SENTINEL = "security_sentinel"
    WORKIQ = "workiq"
    GITHUB_TRACKER = "github_tracker"


@dataclass
class RoutingDecision:
    """Result of intent classification — which agent(s) to dispatch to."""

    primary_agent: str
    secondary_agents: list[str] = field(default_factory=list)
    confidence: float = 0.5
    reasoning: str = ""
    extracted_params: dict[str, Any] = field(default_factory=dict)
    enrichment_applied: bool = False


@dataclass
class RoutingKeywordHint:
    """A keyword match extracted from WorkIQ content for HITL review.

    The user can accept or reject each hint before it influences routing.
    """

    agent_id: str
    keyword: str
    matched_text: str
    source: str = "workiq"


# ── built-in keyword routes ────────────────────────────────────────────────
# These ship with ProtoForge.  Additional patterns can be registered at
# runtime via ``IntentRouter.register_patterns()``.

_BUILTIN_KEYWORD_ROUTES: dict[str, list[str]] = {
    AgentType.PLAN: [
        r"\bplan\b",
        r"\bbreak\s*down\b",
        r"\bdecompose\b",
        r"\bstrategy\b",
        r"\bsteps?\b",
        r"\bapproach\b",
        r"\barchitect\b",
    ],
    AgentType.SUB_PLAN: [
        r"\bresource[s]?\b",
        r"\bdeploy\b",
        r"\bprovision\b",
        r"\binfrastructure\b",
        r"\bconnector[s]?\b",
        r"\bprerequisite[s]?\b",
        r"\bsetup\b",
        r"\bservice\s*principal\b",
        r"\bapp\s*registration\b",
        r"\bsku\b",
    ],
    AgentType.LOG_ANALYSIS: [
        r"\blog[s]?\b",
        r"\berror\s*log\b",
        r"\bstack\s*trace\b",
        r"\btraceback\b",
        r"\bcrash\b",
        r"\bexception\b",
        r"\b500\b",
        r"\b4[0-9]{2}\b",
    ],
    AgentType.CODE_RESEARCH: [
        r"\bcode\b",
        r"\bfunction\b",
        r"\bclass\b",
        r"\bimplement",
        r"\bsearch\s*code\b",
        r"\bfind.*function\b",
        r"\bwhere\s*is\b",
        r"\bsource\b",
    ],
    AgentType.REMEDIATION: [
        r"\bfix\b",
        r"\bremediat",
        r"\bpatch\b",
        r"\bresolve\b",
        r"\brepair\b",
        r"\bhotfix\b",
        r"\bworkaround\b",
        r"\bmitigat",
        r"\bfix\s.*\b(?:error|exception|bug|issue|problem)\b",
        r"\bnull\s*pointer\b",
        r"\bdebug\b",
    ],
    AgentType.KNOWLEDGE_BASE: [
        r"\bdoc[s]?\b",
        r"\bdocument",
        r"\bknowledge\b",
        r"\bwiki\b",
        r"\bhow\s*to\b",
        r"\bexplain\b",
        r"\bwhat\s*is\b",
    ],
    AgentType.DATA_ANALYSIS: [
        r"\bdata\b",
        r"\banalyze\b",
        r"\banalysis\b",
        r"\bmetric[s]?\b",
        r"\bchart\b",
        r"\bgraph\b",
        r"\btrend\b",
        r"\bstatistic",
    ],
    AgentType.SECURITY_SENTINEL: [
        r"\bsecurity\b",
        r"\bvulnerab",
        r"\bcve\b",
        r"\bscan\b",
        r"\baudit\b",
        r"\bpermission\b",
        r"\baccess\s*control\b",
        r"\bthreat\b",
    ],
    AgentType.WORKIQ: [
        r"\bworkiq\b",
        r"\bwork\s*iq\b",
        r"\bm365\b",
        r"\bmicrosoft\s*365\b",
        r"\bmanager\b",
        r"\borg\s*chart\b",
        r"\bdirect\s*report",
        r"\bmeeting[s]?\b",
        r"\bcalendar\b",
        r"\bschedule\b",
        r"\bemail[s]?\b",
        r"\binbox\b",
        r"\bteams\s*(?:chat|channel|message)",
        r"\bsharepoint\b",
        r"\bonedrive\b",
        r"\bwho\s+is\s+my\b",
        r"\bmy\s+(?:team|org|organization)\b",
    ],
    AgentType.GITHUB_TRACKER: [
        r"\bgithub\b",
        r"\bcommit[s]?\b",
        r"\bissue[s]?\b",
        r"\bpull\s*request",
        r"\bpr\b",
        r"\bchangelog\b",
        r"\brelease\s*note",
        r"\bgit\s*log\b",
        r"\bconventional\s*commit",
        r"\bdocument.*commit",
        r"\bcreate\s*issue\b",
        r"\bclose\s*issue\b",
        r"\bwhat\s*(?:did|does).*commit",
    ],
}

# Default agent when nothing matches
_DEFAULT_AGENT: str = AgentType.KNOWLEDGE_BASE


class IntentRouter:
    """Routes user messages to the appropriate subagent(s).

    Uses a two-tier approach:
    1. Fast keyword matching for obvious intents
    2. LLM-based classification for ambiguous queries

    Use :meth:`register_patterns` to add routing keywords for new agents
    discovered from forge manifests at bootstrap time.
    """

    def __init__(self) -> None:
        # str → compiled patterns; starts with built-ins, extended at runtime
        self._compiled_patterns: dict[str, list[re.Pattern[str]]] = {
            agent_id: [re.compile(p, re.IGNORECASE) for p in patterns]
            for agent_id, patterns in _BUILTIN_KEYWORD_ROUTES.items()
        }

    # ── public API ──────────────────────────────────────────────────────

    def register_patterns(self, agent_id: str, patterns: list[str]) -> None:
        """Register (or extend) keyword patterns for *agent_id*.

        Called during bootstrap for every forge manifest that declares
        routing keywords in its ``tags`` or ``skills`` metadata.
        """
        compiled = [re.compile(p, re.IGNORECASE) for p in patterns]
        existing = self._compiled_patterns.get(agent_id, [])
        self._compiled_patterns[agent_id] = existing + compiled

    def deregister_patterns(self, agent_id: str) -> None:
        """Remove all routing patterns for *agent_id*.

        Called when an agent is disabled or unregistered at runtime so
        that the keyword router no longer considers it for routing.
        """
        removed = self._compiled_patterns.pop(agent_id, None)
        if removed is not None:
            logger.info("routing_patterns_removed", agent=agent_id, count=len(removed))

    def get_patterns(self, agent_id: str) -> list[re.Pattern[str]]:
        """Return the compiled routing patterns for *agent_id* (or empty list)."""
        return list(self._compiled_patterns.get(agent_id, []))

    def restore_patterns(self, agent_id: str, compiled: list[re.Pattern[str]]) -> None:
        """Restore previously saved compiled patterns for *agent_id*."""
        self._compiled_patterns[agent_id] = compiled
        logger.info("routing_patterns_restored", agent=agent_id, count=len(compiled))

    @property
    def known_agent_ids(self) -> list[str]:
        """All agent IDs that have at least one routing pattern."""
        return list(self._compiled_patterns)

    def route_by_keywords(self, message: str) -> RoutingDecision:
        """Fast keyword-based routing (no LLM call needed)."""
        scores: dict[str, int] = dict.fromkeys(self._compiled_patterns, 0)

        for agent_id, patterns in self._compiled_patterns.items():
            for pattern in patterns:
                if pattern.search(message):
                    scores[agent_id] += 1

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)

        if ranked[0][1] == 0:
            return RoutingDecision(
                primary_agent=_DEFAULT_AGENT,
                secondary_agents=[],
                confidence=0.3,
                reasoning="No keyword matches; defaulting to knowledge base",
                extracted_params={},
            )

        primary = ranked[0]
        total_score = sum(s for _, s in ranked if s > 0)
        confidence = min(primary[1] / max(total_score, 1), 1.0)

        secondary = [agent_id for agent_id, score in ranked[1:3] if score > 0]

        return RoutingDecision(
            primary_agent=primary[0],
            secondary_agents=secondary,
            confidence=confidence,
            reasoning=f"Keyword match: {primary[0]} scored {primary[1]}/{total_score}",
            extracted_params={},
        )

    def get_llm_routing_prompt(self, message: str) -> str:
        """Generate a prompt for LLM-based intent classification."""
        agent_descriptions: dict[str, str] = {
            AgentType.PLAN: "Task planning, decomposition, strategy, architecture decisions",
            AgentType.SUB_PLAN: (
                "Prerequisite resource planning, infrastructure provisioning, minimum-viable deployments"
            ),
            AgentType.LOG_ANALYSIS: "Log parsing, error analysis, stack traces, crash investigation",
            AgentType.CODE_RESEARCH: "Code search, function lookup, implementation understanding",
            AgentType.REMEDIATION: "Bug fixes, patches, hotfixes, workarounds, resolution steps",
            AgentType.KNOWLEDGE_BASE: "Documentation, how-to guides, explanations, knowledge retrieval",
            AgentType.DATA_ANALYSIS: "Data analysis, metrics, charts, trends, statistical analysis",
            AgentType.SECURITY_SENTINEL: "Security scanning, vulnerability assessment, CVE lookup, audits",
            AgentType.WORKIQ: "Microsoft 365 organisational context — people, calendar, email, documents",
            AgentType.GITHUB_TRACKER: "GitHub commit documentation, issue management, changelogs, release notes",
        }

        agents_str = "\n".join(f"  - {agent_id}: {desc}" for agent_id, desc in agent_descriptions.items())

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

    # ── WorkIQ-enriched routing ─────────────────────────────────────────

    def extract_routing_keywords(self, text: str) -> list[RoutingKeywordHint]:
        """Scan *text* (typically WorkIQ content) for routing keywords.

        Returns a list of :class:`RoutingKeywordHint` items the user can
        review via HITL before they influence the routing decision.
        """
        hints: list[RoutingKeywordHint] = []
        seen: set[tuple[str, str]] = set()

        for agent_id, patterns in self._compiled_patterns.items():
            for pattern in patterns:
                for match in pattern.finditer(text):
                    key = (agent_id, match.group().lower())
                    if key not in seen:
                        seen.add(key)
                        # Grab surrounding context (up to 60 chars each side)
                        start = max(match.start() - 60, 0)
                        end = min(match.end() + 60, len(text))
                        context_snippet = text[start:end].replace("\n", " ").strip()
                        hints.append(
                            RoutingKeywordHint(
                                agent_id=agent_id,
                                keyword=match.group(),
                                matched_text=context_snippet,
                            )
                        )
        return hints

    def route_with_context(
        self,
        message: str,
        enrichment_hints: list[RoutingKeywordHint] | None = None,
    ) -> RoutingDecision:
        """Route combining user *message* keywords with accepted HITL hints.

        When *enrichment_hints* is provided (user-approved keyword hints
        extracted from WorkIQ output) they contribute extra score weight to
        the relevant agents, producing a more informed routing decision.
        """
        scores: dict[str, int] = dict.fromkeys(self._compiled_patterns, 0)

        # Phase 1 — standard keyword scoring on user message
        for agent_id, patterns in self._compiled_patterns.items():
            for pattern in patterns:
                if pattern.search(message):
                    scores[agent_id] += 1

        # Phase 2 — boost from accepted HITL routing hints
        hint_boost: dict[str, int] = {}
        if enrichment_hints:
            for hint in enrichment_hints:
                aid = hint.agent_id
                if aid in scores:
                    scores[aid] += 1
                    hint_boost[aid] = hint_boost.get(aid, 0) + 1

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)

        if ranked[0][1] == 0:
            return RoutingDecision(
                primary_agent=_DEFAULT_AGENT,
                secondary_agents=[],
                confidence=0.3,
                reasoning="No keyword matches; defaulting to knowledge base",
                extracted_params={},
                enrichment_applied=bool(enrichment_hints),
            )

        primary = ranked[0]
        total_score = sum(s for _, s in ranked if s > 0)
        confidence = min(primary[1] / max(total_score, 1), 1.0)
        secondary = [agent_id for agent_id, score in ranked[1:3] if score > 0]

        boost_note = ""
        if hint_boost:
            boost_parts = [f"{a}+{n}" for a, n in hint_boost.items()]
            boost_note = f" | WorkIQ boost: {', '.join(boost_parts)}"

        return RoutingDecision(
            primary_agent=primary[0],
            secondary_agents=secondary,
            confidence=confidence,
            reasoning=(f"Keyword match: {primary[0]} scored {primary[1]}/{total_score}{boost_note}"),
            extracted_params={},
            enrichment_applied=bool(enrichment_hints),
        )

    def get_llm_routing_prompt_with_context(self, message: str, enrichment_text: str) -> str:
        """LLM routing prompt that includes WorkIQ organisational context."""
        base = self.get_llm_routing_prompt(message)
        return (
            base.rstrip('}"')
            + f'\n\nOrganisational context from Work IQ:\n"""\n{enrichment_text}\n"""\n'
            + "Use the organisational context above as additional signal when "
            + "choosing the best agent.\n"
        )
