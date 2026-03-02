"""Provider detection from request path and headers."""

from __future__ import annotations

from agent_interception.config import InterceptorConfig
from agent_interception.models import Provider
from agent_interception.providers.anthropic import AnthropicParser
from agent_interception.providers.base import ProviderParser
from agent_interception.providers.ollama import OllamaParser
from agent_interception.providers.openai import OpenAIParser


class ProviderRegistry:
    """Detects the provider from request path and headers, returns the parser and upstream URL."""

    def __init__(self, config: InterceptorConfig) -> None:
        self._config = config
        self._openai = OpenAIParser()
        self._anthropic = AnthropicParser()
        self._ollama = OllamaParser()

    def detect(self, path: str, headers: dict[str, str]) -> tuple[Provider, ProviderParser, str]:
        """Detect provider from request path and headers.

        Returns (provider, parser, upstream_base_url).
        """
        # Anthropic: /v1/messages (confirmed by anthropic-version header or path)
        if path.startswith("/v1/messages"):
            return (
                Provider.ANTHROPIC,
                self._anthropic,
                self._config.anthropic_base_url,
            )

        # Check for anthropic-version header on other /v1/ paths
        if "anthropic-version" in headers and path.startswith("/v1/"):
            return (
                Provider.ANTHROPIC,
                self._anthropic,
                self._config.anthropic_base_url,
            )

        # Ollama: /api/*
        if path.startswith("/api/"):
            return (
                Provider.OLLAMA,
                self._ollama,
                self._config.ollama_base_url,
            )

        # OpenAI: /v1/* (anything else under /v1/)
        if path.startswith("/v1/"):
            return (
                Provider.OPENAI,
                self._openai,
                self._config.openai_base_url,
            )

        # Internal interceptor endpoints — handled by server.py routes directly,
        # this code path is only reached for paths that slip through (shouldn't happen)
        if path.startswith("/_interceptor/"):
            return Provider.UNKNOWN, self._openai, ""

        # Non-/v1/ paths: route to Ollama (handles HEAD /, GET /api/tags, etc.)
        # OpenAI and Anthropic always use /v1/ prefixes; anything else is Ollama.
        return (
            Provider.OLLAMA,
            self._ollama,
            self._config.ollama_base_url,
        )
