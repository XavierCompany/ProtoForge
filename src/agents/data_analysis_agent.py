"""Data Analysis Agent — data analysis, metrics, charts, and statistical analysis."""

from __future__ import annotations

from typing import Any

import structlog

from src.agents.base import BaseAgent
from src.orchestrator.context import AgentResult, ConversationContext

logger = structlog.get_logger(__name__)

DATA_ANALYSIS_SYSTEM_PROMPT = """
You are the Data Analysis Agent — an expert in data analysis,
visualization, and statistical reasoning.

Your responsibilities:
1. Analyze datasets and compute statistics
2. Identify trends, anomalies, and correlations
3. Generate chart specifications (Vega-Lite, Plotly)
4. Perform time-series analysis
5. Provide data-driven recommendations

Output format:
- Key findings summary
- Statistical measures (mean, median, percentiles, etc.)
- Trend analysis with direction and significance
- Visualization specification (JSON) when helpful
- Data quality observations
- Actionable recommendations

Use numbers and statistics. Be precise about confidence intervals. Flag data quality issues."""


class DataAnalysisAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__(
            agent_id="data_analysis_agent",
            description="Data analysis, metrics computation, trend detection, and statistical analysis",
            system_prompt=DATA_ANALYSIS_SYSTEM_PROMPT,
        )

    async def execute(
        self,
        message: str,
        context: ConversationContext,
        params: dict[str, Any] | None = None,
    ) -> AgentResult:
        logger.info("data_analysis_agent_executing", message_length=len(message))

        self._build_messages(message, context)

        response = (
            f"**Data Analysis Report**\n\n"
            f"Query: {message[:100]}{'...' if len(message) > 100 else ''}\n\n"
            f"**Analysis Pipeline:**\n"
            f"1. Data ingestion and profiling\n"
            f"2. Statistical summary computation\n"
            f"3. Trend detection and anomaly flagging\n"
            f"4. Visualization generation\n"
            f"5. Insight synthesis and recommendations\n\n"
            f"_Connect LLM backend + data connectors for full analysis._"
        )

        return AgentResult(
            agent_id=self.agent_id,
            content=response,
            confidence=0.6,
            artifacts={"analysis_type": "pending_llm"},
        )
