"""Security Sentinel Agent — vulnerability scanning, CVE lookup, and security audits.

Keeps ``_classify_security_concern()`` for fast categorisation before LLM.
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

_DEFAULT_SECURITY_PROMPT = """
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
    def __init__(
        self,
        agent_id: str = "security_sentinel",
        description: str = "Security scanning, vulnerability assessment, CVE lookup, and compliance audits",
        system_prompt: str = _DEFAULT_SECURITY_PROMPT,
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
        _params: dict[str, Any] | None = None,
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
