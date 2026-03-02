"""Data models for intercepted interactions."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Provider(StrEnum):
    """Supported LLM providers."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    OLLAMA = "ollama"
    UNKNOWN = "unknown"


class StreamChunk(BaseModel):
    """A single chunk from an SSE or NDJSON stream."""

    index: int = Field(description="Chunk sequence number")
    timestamp: datetime = Field(description="When this chunk was received")
    data: str = Field(description="Raw chunk data")
    parsed: dict[str, Any] | None = Field(default=None, description="Parsed chunk content")
    delta_text: str | None = Field(default=None, description="Extracted text delta from this chunk")


class TokenUsage(BaseModel):
    """Token usage from the provider response."""

    input_tokens: int | None = Field(default=None, description="Prompt/input tokens")
    output_tokens: int | None = Field(default=None, description="Completion/output tokens")
    cache_creation_tokens: int | None = Field(
        default=None, description="Tokens used for cache creation (Anthropic)"
    )
    cache_read_tokens: int | None = Field(
        default=None, description="Tokens read from cache (Anthropic)"
    )
    total_tokens: int | None = Field(default=None, description="Total tokens")

    @property
    def computed_total(self) -> int:
        """Compute total from input + output if not provided."""
        if self.total_tokens is not None:
            return self.total_tokens
        return (self.input_tokens or 0) + (self.output_tokens or 0)


class ImageMetadata(BaseModel):
    """Metadata about images in requests (not the raw base64)."""

    count: int = Field(description="Number of images")
    media_types: list[str] = Field(default_factory=list, description="Image MIME types")
    approximate_sizes: list[int] = Field(
        default_factory=list, description="Approximate sizes in bytes"
    )


class CostEstimate(BaseModel):
    """Estimated cost of an interaction."""

    input_cost: float = Field(default=0.0, description="Estimated input token cost in USD")
    output_cost: float = Field(default=0.0, description="Estimated output token cost in USD")
    total_cost: float = Field(default=0.0, description="Total estimated cost in USD")
    model: str | None = Field(default=None, description="Model used for cost calculation")
    note: str | None = Field(default=None, description="Notes about the estimate")


class ContextMetrics(BaseModel):
    """Computed metrics about the context window for a request."""

    message_count: int = Field(description="Total messages in the request")
    user_turn_count: int = Field(description="Messages with role=user")
    assistant_turn_count: int = Field(description="Messages with role=assistant")
    tool_result_count: int = Field(description="Messages with role=tool or tool_result")
    context_depth_chars: int = Field(description="Approximate total chars of all message content")
    new_messages_this_turn: int | None = Field(
        default=None, description="Delta message count vs previous turn (None if unknown)"
    )
    system_prompt_length: int = Field(description="Chars in system prompt (0 if none)")
    system_prompt_hash: str | None = Field(
        default=None, description="First 16 hex chars of SHA-256 of system prompt"
    )


class Interaction(BaseModel):
    """A complete intercepted request-response interaction."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str | None = Field(default=None, description="Session ID for grouping interactions")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When the request was received",
    )

    # Request details
    method: str = Field(description="HTTP method")
    path: str = Field(description="Request path")
    request_headers: dict[str, str] = Field(
        default_factory=dict, description="Request headers (keys redacted)"
    )
    request_body: dict[str, Any] | None = Field(default=None, description="Parsed request body")
    raw_request_body: str | None = Field(default=None, description="Raw request body string")

    # Provider info
    provider: Provider = Field(default=Provider.UNKNOWN, description="Detected provider")
    model: str | None = Field(default=None, description="Model name from request")

    # Parsed request content
    system_prompt: str | None = Field(default=None, description="System prompt if present")
    messages: list[dict[str, Any]] | None = Field(default=None, description="Conversation messages")
    tools: list[dict[str, Any]] | None = Field(default=None, description="Tool definitions")
    image_metadata: ImageMetadata | None = Field(
        default=None, description="Image metadata if images present"
    )

    # Response details
    status_code: int | None = Field(default=None, description="Response HTTP status code")
    response_headers: dict[str, str] = Field(default_factory=dict, description="Response headers")
    response_body: dict[str, Any] | None = Field(
        default=None, description="Full response body (reconstructed from stream if streaming)"
    )
    raw_response_body: str | None = Field(default=None, description="Raw response body string")
    is_streaming: bool = Field(default=False, description="Whether response was streamed")

    # Stream data
    stream_chunks: list[StreamChunk] = Field(
        default_factory=list, description="Individual stream chunks"
    )

    # Extracted response content
    response_text: str | None = Field(default=None, description="Final response text content")
    tool_calls: list[dict[str, Any]] | None = Field(
        default=None, description="Tool calls in response"
    )

    # Metrics
    token_usage: TokenUsage | None = Field(default=None, description="Token usage")
    cost_estimate: CostEstimate | None = Field(default=None, description="Cost estimate")
    time_to_first_token_ms: float | None = Field(
        default=None, description="Time to first token in milliseconds"
    )
    total_latency_ms: float | None = Field(
        default=None, description="Total request-response latency in milliseconds"
    )

    # Error info
    error: str | None = Field(default=None, description="Error message if request failed")

    # Conversation threading
    conversation_id: str | None = Field(
        default=None, description="Groups all turns in one conversation thread"
    )
    parent_interaction_id: str | None = Field(
        default=None, description="Previous turn's interaction ID"
    )
    turn_number: int | None = Field(
        default=None, description="1-based turn index within the conversation"
    )
    turn_type: str | None = Field(
        default=None,
        description="Turn classification: initial | continuation | tool_result | handoff",
    )
    context_metrics: ContextMetrics | None = Field(
        default=None, description="Computed context window metrics for this turn"
    )
