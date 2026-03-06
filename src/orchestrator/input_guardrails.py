"""User input sanitization and lightweight prompt-injection guardrails."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_CONTROL_CHARS_PATTERN = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")

_PROMPT_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "ignore_previous_instructions",
        re.compile(r"\bignore\s+(all\s+)?(previous|prior|earlier)\s+(instructions?|prompts?)\b", re.IGNORECASE),
    ),
    (
        "system_prompt_reference",
        re.compile(r"\b(system|developer)\s+prompt\b", re.IGNORECASE),
    ),
    (
        "safety_bypass_attempt",
        re.compile(r"\b(bypass|override)\b.{0,40}\b(safety|guardrails?|policy|instructions?)\b", re.IGNORECASE),
    ),
    (
        "privilege_escalation_roleplay",
        re.compile(r"\b(act|pretend)\s+as\b.{0,40}\b(system|developer|root|admin)\b", re.IGNORECASE),
    ),
    (
        "jailbreak_keyword",
        re.compile(r"\b(jailbreak|do\s+anything\s+now|dan)\b", re.IGNORECASE),
    ),
)


@dataclass(slots=True)
class SanitizedUserInput:
    """Normalized user message plus metadata about applied guardrails."""

    content: str
    flags: list[str] = field(default_factory=list)
    removed_control_chars: int = 0
    was_truncated: bool = False

    def to_memory(self) -> dict[str, object]:
        """Serialize guardrail metadata for ConversationContext memory."""
        return {
            "flags": list(self.flags),
            "removed_control_chars": self.removed_control_chars,
            "was_truncated": self.was_truncated,
            "sanitized_length": len(self.content),
        }


def sanitize_user_message(
    user_message: str,
    *,
    max_chars: int,
    prompt_injection_guard_enabled: bool = True,
) -> SanitizedUserInput:
    """Normalize incoming user text and annotate suspicious prompt patterns."""
    normalized = user_message.replace("\r\n", "\n").replace("\r", "\n")
    sanitized = _CONTROL_CHARS_PATTERN.sub("", normalized)
    removed_control_chars = len(normalized) - len(sanitized)

    flags: list[str] = []
    if removed_control_chars > 0:
        flags.append("control_chars_removed")

    effective_cap = max(1, max_chars)
    was_truncated = len(sanitized) > effective_cap
    if was_truncated:
        sanitized = sanitized[:effective_cap]
        flags.append("input_truncated")

    if prompt_injection_guard_enabled:
        for flag, pattern in _PROMPT_INJECTION_PATTERNS:
            if pattern.search(sanitized):
                flags.append(flag)

    return SanitizedUserInput(
        content=sanitized,
        flags=flags,
        removed_control_chars=removed_control_chars,
        was_truncated=was_truncated,
    )
