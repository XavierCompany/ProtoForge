"""Context Budget Manager — token budget allocation and enforcement.

Reads configuration from forge/_context_window.yaml and provides:
- Per-agent token budget allocation
- Token counting (via tiktoken or character estimate)
- Content truncation strategies (priority, sliding_window, summarize)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Try to use tiktoken for accurate counting; fall back to char estimate
try:
    import tiktoken

    _TIKTOKEN_AVAILABLE = True
except ImportError:
    _TIKTOKEN_AVAILABLE = False


@dataclass
class TokenBudget:
    """Token budget for a single agent."""

    agent_id: str
    max_input: int
    max_output: int
    strategy: str  # priority | sliding_window | summarize


class ContextBudgetManager:
    """Manages token budgets across agents in an orchestration run."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}
        self._budgets: dict[str, TokenBudget] = {}
        self._usage: dict[str, dict[str, int]] = {}  # agent_id -> {input: n, output: n}

        # Token counting
        tc = self.config.get("token_counting", {})
        self._method = tc.get("method", "character_estimate")
        self._model = tc.get("model", "cl100k_base")
        self._encoder = None

        if self._method == "tiktoken" and _TIKTOKEN_AVAILABLE:
            try:
                self._encoder = tiktoken.get_encoding(self._model)
            except Exception:
                logger.warning("tiktoken_encoding_failed", model=self._model)

    # -- Budget allocation --------------------------------------------------

    def allocate(
        self,
        agent_id: str,
        agent_type: str = "specialist",
        override: dict[str, Any] | None = None,
    ) -> TokenBudget:
        """Allocate a token budget for an agent.

        Uses three levels of precedence:
        1. Agent-specific override (from agent.yaml context_budget)
        2. Defaults by agent type (from _context_window.yaml defaults)
        3. Hard-coded fallbacks
        """
        if override:
            budget = TokenBudget(
                agent_id=agent_id,
                max_input=override.get("max_input_tokens", 16000),
                max_output=override.get("max_output_tokens", 8000),
                strategy=override.get("strategy", "priority"),
            )
        else:
            defaults = self.config.get("defaults", {}).get(agent_type, {})
            budget = TokenBudget(
                agent_id=agent_id,
                max_input=defaults.get("max_input_tokens", 16000),
                max_output=defaults.get("max_output_tokens", 8000),
                strategy=defaults.get("strategy", "priority"),
            )

        self._budgets[agent_id] = budget
        self._usage[agent_id] = {"input": 0, "output": 0}
        logger.debug("budget_allocated", agent=agent_id, input=budget.max_input, output=budget.max_output)
        return budget

    def deallocate(self, agent_id: str) -> None:
        """Remove budget allocation and usage tracking for *agent_id*.

        Called when an agent is disabled or unregistered at runtime.
        The freed token headroom becomes available for remaining agents.
        """
        removed_budget = self._budgets.pop(agent_id, None)
        self._usage.pop(agent_id, None)
        if removed_budget:
            logger.info(
                "budget_deallocated",
                agent=agent_id,
                freed_input=removed_budget.max_input,
                freed_output=removed_budget.max_output,
            )

    # -- Token counting -----------------------------------------------------

    def count_tokens(self, text: str) -> int:
        """Count tokens in a string."""
        if self._encoder:
            return len(self._encoder.encode(text))
        # Character estimate: ~4 chars per token
        return max(1, len(text) // 4)

    # -- Budget enforcement -------------------------------------------------

    def fits_budget(self, agent_id: str, text: str, direction: str = "input") -> bool:
        """Check if text fits within the agent's remaining budget."""
        budget = self._budgets.get(agent_id)
        if not budget:
            return True  # no budget = unlimited

        tokens = self.count_tokens(text)
        used = self._usage.get(agent_id, {}).get(direction, 0)
        limit = budget.max_input if direction == "input" else budget.max_output
        return (used + tokens) <= limit

    def record_usage(self, agent_id: str, tokens: int, direction: str = "input") -> None:
        """Record token usage for an agent."""
        if agent_id not in self._usage:
            self._usage[agent_id] = {"input": 0, "output": 0}
        self._usage[agent_id][direction] += tokens

    def remaining(self, agent_id: str, direction: str = "input") -> int:
        """Return remaining token budget for an agent."""
        budget = self._budgets.get(agent_id)
        if not budget:
            return 999_999

        limit = budget.max_input if direction == "input" else budget.max_output
        used = self._usage.get(agent_id, {}).get(direction, 0)
        return max(0, limit - used)

    # -- Truncation strategies ----------------------------------------------

    def truncate(self, agent_id: str, text: str, direction: str = "input") -> str:
        """Truncate text to fit within the agent's budget using the configured strategy."""
        budget = self._budgets.get(agent_id)
        if not budget:
            return text

        limit = budget.max_input if direction == "input" else budget.max_output
        used = self._usage.get(agent_id, {}).get(direction, 0)
        available = max(0, limit - used)
        tokens = self.count_tokens(text)

        if tokens <= available:
            return text

        if budget.strategy == "sliding_window":
            return self._truncate_sliding_window(text, available)
        if budget.strategy == "summarize":
            return self._truncate_summarize(text, available)
        # Default: priority (keep beginning)
        return self._truncate_priority(text, available)

    def _truncate_priority(self, text: str, max_tokens: int) -> str:
        """Keep the beginning (highest priority) content."""
        if self._encoder:
            encoded = self._encoder.encode(text)
            return self._encoder.decode(encoded[:max_tokens])
        # Character estimate
        char_limit = max_tokens * 4
        return text[:char_limit] + "\n\n[… truncated — priority strategy]"

    def _truncate_sliding_window(self, text: str, max_tokens: int) -> str:
        """Keep the end (most recent) content."""
        overlap = self.config.get("strategies", {}).get("sliding_window", {}).get("overlap_tokens", 200)
        keep_tokens = max(max_tokens - overlap, max_tokens // 2)

        if self._encoder:
            encoded = self._encoder.encode(text)
            return self._encoder.decode(encoded[-keep_tokens:])
        char_limit = keep_tokens * 4
        return "[… earlier content truncated — sliding window]\n\n" + text[-char_limit:]

    def _truncate_summarize(self, text: str, max_tokens: int) -> str:
        """Placeholder for LLM-based summarization — falls back to priority truncation."""
        # Full summarize strategy would call the LLM here.
        # For now, fall back to priority truncation with a marker.
        logger.debug("summarize_strategy_fallback", reason="LLM summarization not yet wired")
        return self._truncate_priority(text, max_tokens)

    # -- Reporting ----------------------------------------------------------

    def usage_report(self) -> dict[str, Any]:
        """Return usage summary for all agents."""
        report: dict[str, Any] = {}
        for agent_id, budget in self._budgets.items():
            used = self._usage.get(agent_id, {"input": 0, "output": 0})
            report[agent_id] = {
                "budget": {"input": budget.max_input, "output": budget.max_output, "strategy": budget.strategy},
                "used": used,
                "remaining": {
                    "input": max(0, budget.max_input - used["input"]),
                    "output": max(0, budget.max_output - used["output"]),
                },
            }
        return report
