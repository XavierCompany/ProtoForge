"""Configuration and settings for ProtoForge."""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings


class AuthMethod(str, Enum):
    AZURE_DEFAULT = "azure_default"
    API_KEY = "api_key"


class LLMProvider(str, Enum):
    AZURE_AI_FOUNDRY = "azure_ai_foundry"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"


class LLMConfig(BaseSettings):
    """LLM provider configuration — platform-agnostic across Opus/Codex/Gemini/GPT."""

    # Azure AI Foundry (primary — recommended for quality/cost/throughput)
    azure_endpoint: Optional[str] = Field(None, alias="AZURE_AI_FOUNDRY_ENDPOINT")
    azure_model: str = Field("gpt-5.3-codex", alias="AZURE_AI_FOUNDRY_MODEL")
    azure_api_version: str = Field("2026-01-01", alias="AZURE_AI_FOUNDRY_API_VERSION")

    # OpenAI (GPT-4o for general, Codex 5.3 for code-heavy tasks)
    openai_api_key: Optional[str] = Field(None, alias="OPENAI_API_KEY")
    openai_model: str = Field("codex-5.3", alias="OPENAI_MODEL")

    # Anthropic (latest Opus as default)
    anthropic_api_key: Optional[str] = Field(None, alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field("claude-opus-4-0625", alias="ANTHROPIC_MODEL")

    # Google
    google_api_key: Optional[str] = Field(None, alias="GOOGLE_API_KEY")
    google_model: str = Field("gemini-3-pro", alias="GOOGLE_MODEL")

    # Auth
    auth_method: AuthMethod = Field(AuthMethod.AZURE_DEFAULT, alias="AUTH_METHOD")

    @property
    def active_provider(self) -> LLMProvider:
        """Detect the active LLM provider based on available credentials."""
        if self.azure_endpoint:
            return LLMProvider.AZURE_AI_FOUNDRY
        if self.openai_api_key:
            return LLMProvider.OPENAI
        if self.anthropic_api_key:
            return LLMProvider.ANTHROPIC
        if self.google_api_key:
            return LLMProvider.GOOGLE
        return LLMProvider.AZURE_AI_FOUNDRY  # default

    model_config = {"env_file": ".env", "extra": "ignore"}


class ServerConfig(BaseSettings):
    """HTTP server configuration."""

    host: str = Field("0.0.0.0", alias="SERVER_HOST")
    port: int = Field(8080, alias="SERVER_PORT")

    model_config = {"env_file": ".env", "extra": "ignore"}


class MCPConfig(BaseSettings):
    """MCP server configuration."""

    port: int = Field(8081, alias="MCP_SERVER_PORT")
    skills_dir: Path = Field(Path("./skills"), alias="MCP_SKILLS_DIR")

    model_config = {"env_file": ".env", "extra": "ignore"}


class ObservabilityConfig(BaseSettings):
    """Observability / tracing configuration."""

    otlp_endpoint: str = Field("http://localhost:4317", alias="OTEL_EXPORTER_OTLP_ENDPOINT")
    log_level: str = Field("INFO", alias="LOG_LEVEL")

    model_config = {"env_file": ".env", "extra": "ignore"}


class Settings(BaseSettings):
    """Root settings aggregating all config sections."""

    llm: LLMConfig = Field(default_factory=LLMConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    registry_path: Path = Field(Path("./registry_data"), alias="REGISTRY_PATH")

    model_config = {"env_file": ".env", "extra": "ignore"}


# Singleton
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
