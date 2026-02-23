"""Governance Guardian — always-on context window and skill cap enforcement.

This module provides three enforcement mechanisms that run at every stage
of the orchestration pipeline:

1. **Context Window Guardian** — monitors cumulative token usage across a
   single orchestration run.  Triggers a Human-in-the-Loop review when
   usage crosses the *warning_threshold* (120 K tokens by default) and
   hard-stops execution at the *hard_cap* (128 K).

2. **Skill Cap Enforcer** — validates that no agent declares more than
   *max_skills_per_agent* (4 by default) in its forge manifest.  Fires a
   HITL review suggesting which skills to split into a sub-agent.

3. **Architectural Auditor** — lightweight static check surfaced via
   the :meth:`audit_manifest` method.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from src.forge.context_budget import ContextBudgetManager
    from src.forge.loader import AgentManifest

logger = structlog.get_logger(__name__)


# ── Exceptions ──────────────────────────────────────────────────────────────


class ContextWindowExceededError(Exception):
    """Raised when the hard cap is breached and enforce_hard_cap is enabled."""

    def __init__(self, alert: GovernanceAlert) -> None:
        self.alert = alert
        super().__init__(alert.message)


# ── Enums & data-classes ────────────────────────────────────────────────────


class GovernanceLevel(StrEnum):
    """Severity level for a governance alert."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class GovernanceCategory(StrEnum):
    """Category of governance violation."""

    CONTEXT_WINDOW = "context_window"
    SKILL_CAP = "skill_cap"
    ARCHITECTURE = "architecture"


@dataclass
class GovernanceAlert:
    """A single governance violation or warning."""

    alert_id: str
    category: GovernanceCategory
    level: GovernanceLevel
    agent_id: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    suggestion: str = ""
    resolved: bool = False
    resolution: str = ""


@dataclass
class SkillSplitSuggestion:
    """Suggested skill split when an agent exceeds the skill cap."""

    agent_id: str
    current_skills: list[str]
    keep_skills: list[str]
    overflow_skills: list[str]
    suggested_subagent_id: str


@dataclass
class ContextDecompositionSuggestion:
    """Suggested task decomposition when context window is near capacity."""

    current_tokens: int
    hard_cap: int
    warning_threshold: int
    agent_usage: dict[str, int]
    suggestion: str
    recommended_split_agent: str = ""


# ── GovernanceGuardian ──────────────────────────────────────────────────────


class GovernanceGuardian:
    """Always-on governance enforcement for the orchestration pipeline.

    Instantiated once during bootstrap and injected into the
    :class:`~src.orchestrator.engine.OrchestratorEngine` and the
    :class:`~src.forge.loader.ForgeLoader`.

    Enforcement points
    ──────────────────
    - **Manifest load** — :meth:`validate_skill_cap` checks skill count.
    - **Pre-dispatch**  — :meth:`check_context_window` verifies token budget.
    - **Post-dispatch** — :meth:`record_agent_usage` updates cumulative totals.
    """

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        budget_manager: ContextBudgetManager | None = None,
    ) -> None:
        self._config = config or {}
        self._budget_manager = budget_manager

        # Parse governance section from context_window.yaml
        gov = self._config.get("governance", {})

        # Context window thresholds
        cw = gov.get("context_window", {})
        self._warning_threshold: int = cw.get("warning_threshold", 120_000)
        self._hard_cap: int = cw.get("hard_cap", 128_000)
        self._enforce_hard_cap: bool = cw.get("enforce_hard_cap", True)
        self._check_before: bool = cw.get("check_before_dispatch", True)
        self._check_after: bool = cw.get("check_after_dispatch", True)

        # Skill cap
        sc = gov.get("skill_cap", {})
        self._max_skills: int = sc.get("max_skills_per_agent", 4)
        self._allow_skill_override: bool = sc.get("allow_override", True)

        # HITL settings
        hitl = gov.get("hitl", {})
        self._timeout: float = float(hitl.get("timeout_seconds", 120))

        # Cumulative token tracking for the current run
        self._cumulative_tokens: int = 0
        self._agent_token_usage: dict[str, int] = {}

        # Alert log
        self._alerts: list[GovernanceAlert] = []
        self._alert_counter: int = 0

        # Skill cap violations (pending HITL)
        self._skill_violations: dict[str, SkillSplitSuggestion] = {}

        # Context decomposition suggestions (pending HITL)
        self._context_suggestions: dict[str, ContextDecompositionSuggestion] = {}

        logger.info(
            "governance_guardian_initialised",
            warning_threshold=self._warning_threshold,
            hard_cap=self._hard_cap,
            max_skills=self._max_skills,
        )

    # ── Context Window Enforcement ──────────────────────────────────────

    def check_context_window(
        self,
        agent_id: str,
        estimated_input_tokens: int,
    ) -> GovernanceAlert | None:
        """Pre-dispatch check: will adding *estimated_input_tokens* breach a threshold?

        Returns a :class:`GovernanceAlert` if the warning threshold is
        breached, or raises :class:`ContextWindowExceededError` if the
        hard cap would be exceeded.  Returns ``None`` if healthy.
        """
        if not self._check_before:
            return None

        projected = self._cumulative_tokens + estimated_input_tokens

        if projected >= self._hard_cap:
            alert = self._create_alert(
                category=GovernanceCategory.CONTEXT_WINDOW,
                level=GovernanceLevel.CRITICAL,
                agent_id=agent_id,
                message=(
                    f"Context window hard cap would be exceeded: "
                    f"{projected:,} ≥ {self._hard_cap:,} tokens. "
                    f"Task must be decomposed before proceeding."
                ),
                suggestion=self._build_decomposition_suggestion(agent_id, projected),
            )
            logger.critical(
                "context_window_hard_cap",
                agent_id=agent_id,
                projected=projected,
                hard_cap=self._hard_cap,
            )
            if self._enforce_hard_cap:
                raise ContextWindowExceededError(alert)
            return alert

        if projected >= self._warning_threshold:
            alert = self._create_alert(
                category=GovernanceCategory.CONTEXT_WINDOW,
                level=GovernanceLevel.WARNING,
                agent_id=agent_id,
                message=(
                    f"Context window approaching limit: "
                    f"{projected:,} / {self._hard_cap:,} tokens "
                    f"(warning at {self._warning_threshold:,}). "
                    f"Consider decomposing the task into a sub-agent."
                ),
                suggestion=self._build_decomposition_suggestion(agent_id, projected),
            )
            logger.warning(
                "context_window_warning",
                agent_id=agent_id,
                projected=projected,
                threshold=self._warning_threshold,
            )
            return alert

        return None

    def record_agent_usage(self, agent_id: str, tokens_used: int) -> None:
        """Post-dispatch: record how many tokens an agent actually consumed."""
        self._cumulative_tokens += tokens_used
        prev = self._agent_token_usage.get(agent_id, 0)
        self._agent_token_usage[agent_id] = prev + tokens_used

        logger.debug(
            "governance_usage_recorded",
            agent_id=agent_id,
            tokens=tokens_used,
            cumulative=self._cumulative_tokens,
        )

    def reset_run(self) -> None:
        """Reset cumulative counters at the start of a new orchestration run."""
        self._cumulative_tokens = 0
        self._agent_token_usage.clear()
        # Keep alerts and skill violations for HITL inspection

    # ── Skill Cap Enforcement ───────────────────────────────────────────

    def validate_skill_cap(self, manifest: AgentManifest) -> GovernanceAlert | None:
        """Check whether an agent manifest exceeds the skill cap.

        Called during :meth:`ForgeLoader._load_agent_dir`.  Returns an
        alert if the cap is exceeded.
        """
        skill_count = len(manifest.skills)
        if skill_count <= self._max_skills:
            return None

        # Build skill split suggestion
        keep = manifest.skills[: self._max_skills]
        overflow = manifest.skills[self._max_skills :]
        suggested_sub_id = f"{manifest.id}_overflow"

        suggestion = SkillSplitSuggestion(
            agent_id=manifest.id,
            current_skills=list(manifest.skills),
            keep_skills=keep,
            overflow_skills=overflow,
            suggested_subagent_id=suggested_sub_id,
        )
        self._skill_violations[manifest.id] = suggestion

        alert = self._create_alert(
            category=GovernanceCategory.SKILL_CAP,
            level=GovernanceLevel.WARNING,
            agent_id=manifest.id,
            message=(
                f"Agent '{manifest.id}' has {skill_count} skills "
                f"(max {self._max_skills}). "
                f"Suggest creating sub-agent '{suggested_sub_id}' "
                f"with overflow skills: {overflow}"
            ),
            suggestion=(
                f"Keep [{', '.join(keep)}] on '{manifest.id}', "
                f"move [{', '.join(overflow)}] to new sub-agent "
                f"'{suggested_sub_id}'."
            ),
            details={
                "keep_skills": keep,
                "overflow_skills": overflow,
                "suggested_subagent_id": suggested_sub_id,
            },
        )

        logger.warning(
            "skill_cap_exceeded",
            agent_id=manifest.id,
            skill_count=skill_count,
            max_skills=self._max_skills,
            overflow=overflow,
        )
        return alert

    # ── Architectural Audit ─────────────────────────────────────────────

    def audit_manifest(self, manifest: AgentManifest) -> list[GovernanceAlert]:
        """Run lightweight architectural checks on an agent manifest.

        Currently checks:
        - Skill cap (delegates to :meth:`validate_skill_cap`)
        - Sub-agent declarations present when heavy context is likely
        """
        alerts: list[GovernanceAlert] = []

        # Skill cap
        skill_alert = self.validate_skill_cap(manifest)
        if skill_alert:
            alerts.append(skill_alert)

        # Context budget hints
        cb = manifest.context_budget
        max_input = cb.get("max_input_tokens", 16_000)
        if max_input > 64_000 and not manifest.subagents:
            alerts.append(
                self._create_alert(
                    category=GovernanceCategory.ARCHITECTURE,
                    level=GovernanceLevel.INFO,
                    agent_id=manifest.id,
                    message=(
                        f"Agent '{manifest.id}' has a large input budget "
                        f"({max_input:,} tokens) but no sub-agents declared. "
                        f"Consider isolating context-heavy work in sub-agents."
                    ),
                    suggestion=(
                        f"Create a sub-agent under '{manifest.id}' for "
                        f"context-heavy tasks to keep the parent window light."
                    ),
                )
            )

        return alerts

    # ── Query & Status ──────────────────────────────────────────────────

    @property
    def cumulative_tokens(self) -> int:
        return self._cumulative_tokens

    @property
    def warning_threshold(self) -> int:
        return self._warning_threshold

    @property
    def hard_cap(self) -> int:
        return self._hard_cap

    @property
    def enforce_hard_cap(self) -> bool:
        return self._enforce_hard_cap

    @property
    def max_skills(self) -> int:
        return self._max_skills

    @property
    def alerts(self) -> list[GovernanceAlert]:
        return list(self._alerts)

    def unresolved_alerts(self) -> list[GovernanceAlert]:
        return [a for a in self._alerts if not a.resolved]

    def agent_token_usage(self) -> dict[str, int]:
        return dict(self._agent_token_usage)

    def get_skill_violation(self, agent_id: str) -> SkillSplitSuggestion | None:
        return self._skill_violations.get(agent_id)

    def get_context_suggestion(self, alert_id: str) -> ContextDecompositionSuggestion | None:
        return self._context_suggestions.get(alert_id)

    def resolve_alert(self, alert_id: str, resolution: str = "accepted") -> bool:
        """Mark an alert as resolved (via HITL or auto-resolve)."""
        for alert in self._alerts:
            if alert.alert_id == alert_id and not alert.resolved:
                alert.resolved = True
                alert.resolution = resolution
                logger.info(
                    "governance_alert_resolved",
                    alert_id=alert_id,
                    resolution=resolution,
                )
                return True
        return False

    def governance_report(self) -> dict[str, Any]:
        """Full governance status for health checks and the inspector."""
        return {
            "cumulative_tokens": self._cumulative_tokens,
            "hard_cap": self._hard_cap,
            "warning_threshold": self._warning_threshold,
            "utilisation_pct": round(
                (self._cumulative_tokens / self._hard_cap * 100) if self._hard_cap else 0,
                1,
            ),
            "agent_usage": dict(self._agent_token_usage),
            "max_skills_per_agent": self._max_skills,
            "total_alerts": len(self._alerts),
            "unresolved_alerts": len(self.unresolved_alerts()),
            "skill_violations": list(self._skill_violations.keys()),
        }

    # ── Private helpers ─────────────────────────────────────────────────

    def _create_alert(
        self,
        *,
        category: GovernanceCategory,
        level: GovernanceLevel,
        agent_id: str,
        message: str,
        suggestion: str = "",
        details: dict[str, Any] | None = None,
    ) -> GovernanceAlert:
        self._alert_counter += 1
        alert_id = f"gov-{self._alert_counter:04d}"
        alert = GovernanceAlert(
            alert_id=alert_id,
            category=category,
            level=level,
            agent_id=agent_id,
            message=message,
            details=details or {},
            suggestion=suggestion,
        )
        self._alerts.append(alert)
        return alert

    def _build_decomposition_suggestion(self, agent_id: str, projected_tokens: int) -> str:
        """Build a human-readable suggestion for context decomposition."""
        usage_lines = [
            f"  - {aid}: {tok:,} tokens" for aid, tok in sorted(self._agent_token_usage.items(), key=lambda x: -x[1])
        ]
        usage_summary = "\n".join(usage_lines) if usage_lines else "  (no per-agent data yet)"

        suggestion_text = (
            f"Decompose the current task: create a sub-agent to handle "
            f"remaining work in a fresh context window.\n"
            f"Current usage: {projected_tokens:,} / {self._hard_cap:,} tokens.\n"
            f"Per-agent breakdown:\n{usage_summary}\n"
            f"Triggering agent: {agent_id}"
        )

        # Store as a pending decomposition suggestion
        decomp = ContextDecompositionSuggestion(
            current_tokens=projected_tokens,
            hard_cap=self._hard_cap,
            warning_threshold=self._warning_threshold,
            agent_usage=dict(self._agent_token_usage),
            suggestion=suggestion_text,
            recommended_split_agent=f"{agent_id}_overflow",
        )
        alert_id = f"gov-{self._alert_counter:04d}"
        self._context_suggestions[alert_id] = decomp

        return suggestion_text
