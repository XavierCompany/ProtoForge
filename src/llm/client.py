"""Async LLM client — Azure AI Foundry via DefaultAzureCredential.

This module provides a thin, lazy-initialised wrapper around the OpenAI
Python SDK configured for Azure AI Foundry.  The **primary** auth
mechanism is ``DefaultAzureCredential`` (``az login``) so that no API
keys need to be committed or distributed.

When no Azure endpoint is configured (or auth fails), every public
method returns ``None`` so that callers can gracefully fall back to
their existing heuristic / placeholder logic.

Singleton access::

    from src.llm.client import get_llm_client
    client = get_llm_client()
    text = await client.chat(messages)   # str | None
"""

from __future__ import annotations

from typing import Any

import structlog

from src.config import AuthMethod, LLMProvider, get_settings

logger = structlog.get_logger(__name__)


class LLMClient:
    """Async LLM client supporting Azure AI Foundry and direct OpenAI.

    Lazy-initialises the underlying SDK client on first ``chat()`` call.
    If construction fails (missing endpoint, auth error) the client stays
    ``None`` and all calls return ``None`` — ensuring silent degradation.
    """

    def __init__(self) -> None:
        self._client: Any | None = None
        self._initialised = False
        self._available = False

    # ── Public API ──────────────────────────────────────────────────────

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> str | None:
        """Send *messages* to the configured LLM and return assistant text.

        Returns ``None`` when:
        - No LLM endpoint is configured.
        - Authentication fails.
        - The API call errors out (network, quota, etc.).

        Callers should always have a fallback path.
        """
        if not self._initialised:
            self._init_client()

        if not self._available or self._client is None:
            return None

        settings = get_settings()
        resolved_model = model or self._resolve_model(settings)

        try:
            # Newer models (gpt-5*, o1*, o3*) require max_completion_tokens
            # instead of the deprecated max_tokens parameter.
            token_kwarg = (
                {"max_completion_tokens": max_tokens}
                if self._uses_max_completion_tokens(resolved_model)
                else {"max_tokens": max_tokens}
            )
            # Some models only accept the default temperature (1).
            temp_kwarg: dict[str, float] = (
                {"temperature": temperature} if self._supports_temperature(resolved_model) else {}
            )
            response = await self._client.chat.completions.create(
                model=resolved_model,
                messages=messages,
                **token_kwarg,
                **temp_kwarg,
            )
            content: str | None = response.choices[0].message.content
            logger.debug(
                "llm_chat_ok",
                model=resolved_model,
                prompt_messages=len(messages),
                response_length=len(content) if content else 0,
            )
            return content or None
        except Exception as exc:
            logger.warning("llm_chat_failed", error=str(exc), model=resolved_model)
            return None

    @property
    def available(self) -> bool:
        """Whether the LLM backend is configured and reachable."""
        if not self._initialised:
            self._init_client()
        return self._available

    # ── Internal ────────────────────────────────────────────────────────

    def _init_client(self) -> None:
        """Lazily build the SDK client based on settings.

        Called once on the first ``chat()`` invocation.  Sets
        ``self._available`` so subsequent calls skip immediately if there
        is no usable backend.
        """
        self._initialised = True
        settings = get_settings()
        llm = settings.llm
        provider = llm.active_provider

        try:
            if provider == LLMProvider.AZURE_AI_FOUNDRY:
                self._init_azure(llm)
            elif provider == LLMProvider.OPENAI:
                self._init_openai(llm)
            else:
                # Anthropic / Google: not yet implemented via OpenAI compat
                logger.info("llm_provider_not_implemented", provider=provider)
                return
        except Exception as exc:
            logger.warning("llm_init_failed", provider=provider, error=str(exc))
            return

        self._available = self._client is not None

    def _init_azure(self, llm: Any) -> None:
        """Initialise ``AsyncAzureOpenAI`` with DefaultAzureCredential or key."""
        if not llm.azure_endpoint:
            logger.info("llm_azure_no_endpoint", hint="Set AZURE_AI_FOUNDRY_ENDPOINT")
            return

        from openai import AsyncAzureOpenAI

        if llm.auth_method == AuthMethod.AZURE_DEFAULT:
            from azure.identity import DefaultAzureCredential, get_bearer_token_provider

            credential = DefaultAzureCredential()
            token_provider = get_bearer_token_provider(
                credential,
                "https://cognitiveservices.azure.com/.default",
            )
            self._client = AsyncAzureOpenAI(
                azure_endpoint=llm.azure_endpoint,
                azure_ad_token_provider=token_provider,
                api_version=llm.azure_api_version,
            )
            logger.info(
                "llm_azure_ready",
                endpoint=llm.azure_endpoint,
                auth="DefaultAzureCredential",
                api_version=llm.azure_api_version,
            )
        else:
            # API-key fallback
            if not llm.openai_api_key:
                logger.info("llm_azure_no_key", hint="Set OPENAI_API_KEY for api_key auth")
                return
            self._client = AsyncAzureOpenAI(
                azure_endpoint=llm.azure_endpoint,
                api_key=llm.openai_api_key,
                api_version=llm.azure_api_version,
            )
            logger.info(
                "llm_azure_ready",
                endpoint=llm.azure_endpoint,
                auth="api_key",
                api_version=llm.azure_api_version,
            )

    def _init_openai(self, llm: Any) -> None:
        """Initialise ``AsyncOpenAI`` with an API key."""
        if not llm.openai_api_key:
            logger.info("llm_openai_no_key", hint="Set OPENAI_API_KEY")
            return

        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=llm.openai_api_key)
        logger.info("llm_openai_ready", model=llm.openai_model)

    @staticmethod
    def _uses_max_completion_tokens(model: str) -> bool:
        """Return True for models that require ``max_completion_tokens``.

        Newer OpenAI models (gpt-5.x, o1, o3, etc.) reject the legacy
        ``max_tokens`` parameter and require ``max_completion_tokens``.
        """
        m = model.lower()
        return m.startswith(("gpt-5", "o1", "o3"))

    @staticmethod
    def _supports_temperature(model: str) -> bool:
        """Return True if the model accepts a custom ``temperature`` value.

        Some newer models (gpt-5.x, o1, o3) only support the default
        temperature (1) and reject any other value.
        """
        m = model.lower()
        return not m.startswith(("gpt-5", "o1", "o3"))

    @staticmethod
    def _resolve_model(settings: Any) -> str:
        """Pick the model string based on the active provider."""
        llm = settings.llm
        provider = llm.active_provider
        if provider == LLMProvider.AZURE_AI_FOUNDRY:
            return llm.azure_model
        if provider == LLMProvider.OPENAI:
            return llm.openai_model
        return llm.azure_model  # safe default


# ── Singleton ───────────────────────────────────────────────────────────

_llm_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    """Return the module-level :class:`LLMClient` singleton."""
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client
