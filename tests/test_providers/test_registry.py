"""Tests for provider registry."""

from __future__ import annotations

from agent_interception.config import InterceptorConfig
from agent_interception.models import Provider
from agent_interception.providers.registry import ProviderRegistry


def make_registry() -> ProviderRegistry:
    return ProviderRegistry(InterceptorConfig())


class TestDetect:
    def test_anthropic_messages(self) -> None:
        reg = make_registry()
        provider, _, url = reg.detect("/v1/messages", {})
        assert provider == Provider.ANTHROPIC
        assert url == "https://api.anthropic.com"

    def test_anthropic_messages_with_params(self) -> None:
        reg = make_registry()
        provider, _, _url = reg.detect("/v1/messages?beta=true", {})
        assert provider == Provider.ANTHROPIC

    def test_anthropic_header_on_other_v1(self) -> None:
        reg = make_registry()
        provider, _, _ = reg.detect("/v1/complete", {"anthropic-version": "2023-06-01"})
        assert provider == Provider.ANTHROPIC

    def test_openai_chat_completions(self) -> None:
        reg = make_registry()
        provider, _, url = reg.detect("/v1/chat/completions", {})
        assert provider == Provider.OPENAI
        assert url == "https://api.openai.com"

    def test_openai_embeddings(self) -> None:
        reg = make_registry()
        provider, _, _ = reg.detect("/v1/embeddings", {})
        assert provider == Provider.OPENAI

    def test_ollama_chat(self) -> None:
        reg = make_registry()
        provider, _, url = reg.detect("/api/chat", {})
        assert provider == Provider.OLLAMA
        assert url == "http://localhost:11434"

    def test_ollama_generate(self) -> None:
        reg = make_registry()
        provider, _, _ = reg.detect("/api/generate", {})
        assert provider == Provider.OLLAMA

    def test_root_head_routes_to_ollama(self) -> None:
        reg = make_registry()
        provider, _, url = reg.detect("/", {})
        assert provider == Provider.OLLAMA
        assert url == "http://localhost:11434"

    def test_root_get_routes_to_ollama(self) -> None:
        reg = make_registry()
        provider, _, url = reg.detect("/", {})
        assert provider == Provider.OLLAMA
        assert url == "http://localhost:11434"

    def test_non_v1_path_routes_to_ollama(self) -> None:
        reg = make_registry()
        provider, _, url = reg.detect("/some-other-path", {})
        assert provider == Provider.OLLAMA
        assert url == "http://localhost:11434"

    def test_internal_endpoint(self) -> None:
        reg = make_registry()
        provider, _, url = reg.detect("/_interceptor/health", {})
        assert provider == Provider.UNKNOWN
        assert url == ""

    def test_custom_upstream_urls(self) -> None:
        config = InterceptorConfig(
            openai_base_url="http://custom-openai:8000",
            anthropic_base_url="http://custom-anthropic:8001",
            ollama_base_url="http://custom-ollama:8002",
        )
        reg = ProviderRegistry(config)

        _, _, url = reg.detect("/v1/chat/completions", {})
        assert url == "http://custom-openai:8000"

        _, _, url = reg.detect("/v1/messages", {})
        assert url == "http://custom-anthropic:8001"

        _, _, url = reg.detect("/api/chat", {})
        assert url == "http://custom-ollama:8002"
