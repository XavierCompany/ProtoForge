"""Log Analysis Agent — log parsing, error analysis, and crash investigation.

Keeps a lightweight ``_detect_patterns()`` helper that performs fast regex
checks before handing off to the LLM.  System prompt is sourced from the
forge manifest when available.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import structlog

from src.agents.base import BaseAgent
from src.orchestrator.context import AgentResult, ConversationContext

if TYPE_CHECKING:
    from src.forge.loader import AgentManifest

logger = structlog.get_logger(__name__)

_DEFAULT_LOG_PROMPT = """
You are the Log Analysis Agent — an expert in parsing, analyzing,
and diagnosing application logs.

Your responsibilities:
1. Parse structured and unstructured log formats (JSON, syslog, plaintext)
2. Identify error patterns, anomalies, and recurring failures
3. Extract stack traces and map them to root causes
4. Correlate events across distributed services
5. Provide severity classification and impact assessment

Output format:
- Error summary with severity (CRITICAL/HIGH/MEDIUM/LOW)
- Root cause analysis
- Timeline of events (if multiple log entries)
- Affected components/services
- Recommended next steps

Be precise. Quote exact log lines. Identify patterns statistically when possible."""


class LogAnalysisAgent(BaseAgent):
    def __init__(
        self,
        agent_id: str = "log_analysis",
        description: str = "Log parsing, error analysis, stack traces, and crash investigation",
        system_prompt: str = _DEFAULT_LOG_PROMPT,
        *,
        manifest: AgentManifest | None = None,
    ) -> None:
        super().__init__(
            agent_id=agent_id,
            description=description,
            system_prompt=system_prompt,
            manifest=manifest,
        )

    async def execute(
        self,
        message: str,
        context: ConversationContext,
        params: dict[str, Any] | None = None,
    ) -> AgentResult:
        logger.info("log_analysis_agent_executing", message_length=len(message))

        # Quick pattern detection for common log signatures
        patterns_found = self._detect_patterns(message)

        self._build_messages(message, context)

        response = (
            f"**Log Analysis Report**\n\n"
            f"**Patterns Detected:** {len(patterns_found)}\n"
        )

        if patterns_found:
            response += "\n".join(f"- {p}" for p in patterns_found)
        else:
            response += "- No immediate patterns detected in the provided text\n"

        response += (
            "\n\n**Severity:** Requires LLM analysis for full assessment\n"
            "**Next Steps:** Connect LLM backend for deep log analysis\n"
        )

        return AgentResult(
            agent_id=self.agent_id,
            content=response,
            confidence=0.7 if patterns_found else 0.4,
            artifacts={"patterns_found": patterns_found},
        )

    def _detect_patterns(self, text: str) -> list[str]:
        """Quick regex-based pattern detection for common log signatures."""
        patterns: list[str] = []

        if re.search(r"(?i)\b(exception|error|traceback|fatal)\b", text):
            patterns.append("Error/Exception keywords detected")
        if re.search(r"\b[45]\d{2}\b", text):
            patterns.append("HTTP error status codes found")
        if re.search(r"(?i)(null\s*pointer|segfault|out\s*of\s*memory|oom)", text):
            patterns.append("Critical runtime error signature")
        if re.search(r"(?i)(timeout|connection\s*refused|ECONNREFUSED)", text):
            patterns.append("Network/connectivity issue")
        if re.search(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}", text):
            patterns.append("Timestamp patterns found — timeline analysis possible")

        return patterns
