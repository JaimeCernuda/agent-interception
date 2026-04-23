"""Configuration for the interceptor proxy."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings


class InterceptorConfig(BaseSettings):
    """Interceptor proxy configuration, loaded from env vars or CLI args."""

    model_config = {"env_prefix": "INTERCEPTOR_"}

    host: str = Field(default="127.0.0.1", description="Host to bind the proxy to")
    port: int = Field(default=8080, description="Port to bind the proxy to")

    # Upstream defaults (overridden by provider detection)
    openai_base_url: str = Field(
        default="https://api.openai.com",
        description="Default upstream for OpenAI-compatible requests",
    )
    anthropic_base_url: str = Field(
        default="https://api.anthropic.com",
        description="Default upstream for Anthropic requests",
    )
    ollama_base_url: str = Field(
        default="http://localhost:11434",
        description="Default upstream for Ollama requests",
    )

    # Storage
    db_path: str = Field(
        default="interceptor.db",
        description="Path to SQLite database file",
    )
    store_stream_chunks: bool = Field(
        default=True,
        description="Whether to store individual stream chunks (can be large)",
    )

    # Display
    verbose: bool = Field(default=False, description="Verbose terminal output")
    quiet: bool = Field(default=False, description="Suppress terminal output")

    # Redaction
    redact_api_keys: bool = Field(
        default=True,
        description="Redact API keys from logged headers",
    )

    # Prompt-cache injection (Anthropic only)
    inject_prompt_cache: bool = Field(
        default=False,
        description=(
            "Inject Anthropic prompt-cache markers on the system+tools prefix "
            "before forwarding upstream. Dramatically reduces billed input "
            "tokens on tool-loop-heavy agents."
        ),
    )
