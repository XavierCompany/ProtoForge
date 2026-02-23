"""Governance package — context window + skill cap enforcement + agent lifecycle.

Modules
-------
guardian    GovernanceGuardian — token budget enforcement (110K warning, 128K hard cap),
            skill cap enforcement (max 4/agent), architectural audit, HITL alerts.
            Raises ContextWindowExceededError at hard cap.
selector    GovernanceSelector — agent lifecycle HITL (disable/enable/unregister).
            Fail-CLOSED on timeout (auto-reject disable/remove requests).
"""

__all__ = [
    "ContextWindowExceededError",
    "GovernanceGuardian",
    "GovernanceSelector",
]
