"""Security Sentinel Agent — vulnerability scanning, CVE lookup, and security audits."""

from __future__ import annotations

from typing import Any

import structlog

from src.agents.base import BaseAgent
from src.orchestrator.context import AgentResult, ConversationContext

logger = structlog.get_logger(__name__)

SECURITY_SYSTEM_PROMPT = """
You are the Security Sentinel Agent — an expert in application security,
vulnerability assessment, and threat analysis.

Your responsibilities:
1. Scan code for security vulnerabilities (OWASP Top 10, CWE)
2. Look up CVEs and assess their impact
3. Audit access controls and authentication flows
4. Review dependency security (supply chain)
5. Provide remediation guidance for security issues

Output format:
- Security findings with severity (CRITICAL/HIGH/MEDIUM/LOW/INFO)
- CVE references where applicable
- CVSS scores when available
- Affected components and attack vectors
- Remediation steps with priority
- Compliance implications (SOC2, PCI, HIPAA if relevant)

Be thorough but avoid false positives. Prioritize by actual risk, not theoretical risk."""


class SecuritySentinelAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__(
            agent_id="security_sentinel_agent",
            description="Security scanning, vulnerability assessment, CVE lookup, and compliance audits",
            system_prompt=SECURITY_SYSTEM_PROMPT,
        )

    async def execute(
        self,
        message: str,
        context: ConversationContext,
        params: dict[str, Any] | None = None,
    ) -> AgentResult:
        logger.info("security_sentinel_executing", message_length=len(message))

        self._build_messages(message, context)

        # Quick security keyword classification
        categories = self._classify_security_concern(message)

        response = (
            f"**Security Sentinel Report**\n\n"
            f"**Concern Categories:** {', '.join(categories) if categories else 'General security assessment'}\n\n"
            f"**Assessment Pipeline:**\n"
            f"1. Static analysis for common vulnerability patterns\n"
            f"2. Dependency audit (CVE database lookup)\n"
            f"3. Configuration security review\n"
            f"4. Access control and authentication audit\n"
            f"5. Threat model assessment\n\n"
            f"_Connect LLM backend + security databases for full scanning._"
        )

        return AgentResult(
            agent_id=self.agent_id,
            content=response,
            confidence=0.7 if categories else 0.5,
            artifacts={"security_categories": categories},
        )

    def _classify_security_concern(self, text: str) -> list[str]:
        """Classify the security concern into categories."""
        import re

        categories: list[str] = []
        checks = {
            "Injection": r"(?i)(inject|sql|xss|command\s*inject)",
            "Authentication": r"(?i)(auth|login|password|credential|token|oauth|jwt)",
            "Authorization": r"(?i)(access\s*control|permission|rbac|role|privilege)",
            "Cryptography": r"(?i)(encrypt|decrypt|hash|cert|tls|ssl|crypto)",
            "Supply Chain": r"(?i)(dependency|package|npm|pip|supply\s*chain|sbom)",
            "Configuration": r"(?i)(config|secret|env|exposed|leak|misconfigur)",
            "CVE/Vulnerability": r"(?i)(cve|vulnerab|exploit|patch|zero[- ]day)",
        }

        for category, pattern in checks.items():
            if re.search(pattern, text):
                categories.append(category)

        return categories
