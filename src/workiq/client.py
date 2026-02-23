"""WorkIQ CLI subprocess client.

Wraps ``workiq ask -q <question>`` as an async subprocess call so the
orchestrator can query Microsoft 365 Copilot / Work IQ from any agent.

The client returns structured :class:`WorkIQResult` objects that can be
presented to the user for selection before being fed into the orchestrator
pipeline as enrichment context.
"""

from __future__ import annotations

import asyncio
import re
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog

logger = structlog.get_logger(__name__)

# Timeout for a single workiq call (seconds)
_DEFAULT_TIMEOUT = 30


@dataclass
class WorkIQResult:
    """Structured result from a Work IQ query.

    Attributes:
        query: The original question sent to Work IQ.
        content: The raw Markdown response from Work IQ.
        sections: Parsed logical sections (paragraphs / bullet groups).
        sources: Extracted source links ``[n](url)`` from the response.
        timestamp: When the query was made.
        error: Non-empty if the CLI call failed.
    """

    query: str
    content: str = ""
    sections: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.content)


def _parse_sections(text: str) -> list[str]:
    """Split Work IQ Markdown output into logical sections.

    Sections are separated by blank lines, markdown headings, or ``---``
    dividers.  Each section is a candidate for human selection.
    """
    if not text.strip():
        return []

    # Normalise line endings
    text = text.replace("\r\n", "\n")

    # Split on blank lines or horizontal rules
    raw_blocks = re.split(r"\n{2,}|^---+$", text, flags=re.MULTILINE)
    return [block.strip() for block in raw_blocks if block.strip()]


def _extract_sources(text: str) -> list[str]:
    """Extract ``[n](url)`` style source links from the response."""
    return re.findall(r"\[(?:\d+)\]\(([^)]+)\)", text)


class WorkIQClient:
    """Async client around the ``workiq`` CLI.

    Usage::

        client = WorkIQClient()
        result = await client.ask("who is my manager?")
        if result.ok:
            print(result.sections)
    """

    def __init__(self, timeout: int = _DEFAULT_TIMEOUT) -> None:
        self._timeout = timeout
        self._binary = shutil.which("workiq")

    @property
    def available(self) -> bool:
        """Check whether the ``workiq`` binary is on PATH."""
        return self._binary is not None

    async def ask(self, question: str) -> WorkIQResult:
        """Send a question to Work IQ and return a structured result."""
        if not self.available:
            return WorkIQResult(
                query=question,
                error="workiq CLI not found on PATH. Install via: npm install -g @microsoft/workiq",
            )

        cmd = [self._binary, "ask", "-q", question]  # type: ignore[list-item]
        logger.info("workiq_query", question=question[:80])

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)

            raw = stdout.decode("utf-8", errors="replace").strip()
            err = stderr.decode("utf-8", errors="replace").strip()

            if proc.returncode != 0:
                logger.warning("workiq_error", returncode=proc.returncode, stderr=err)
                return WorkIQResult(query=question, error=err or f"exit code {proc.returncode}")

            sections = _parse_sections(raw)
            sources = _extract_sources(raw)

            logger.info(
                "workiq_result",
                sections=len(sections),
                sources=len(sources),
                content_len=len(raw),
            )

            return WorkIQResult(
                query=question,
                content=raw,
                sections=sections,
                sources=sources,
            )

        except TimeoutError:
            logger.warning("workiq_timeout", timeout=self._timeout)
            return WorkIQResult(query=question, error=f"Timed out after {self._timeout}s")
        except Exception as exc:
            logger.error("workiq_unexpected", error=str(exc))
            return WorkIQResult(query=question, error=str(exc))
