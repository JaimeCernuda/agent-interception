"""Tests for the SQLite interaction store."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agent_interception.models import AgentGraph, Interaction, Provider
from agent_interception.storage.store import InteractionStore


@pytest.mark.asyncio
async def test_save_and_get(store: InteractionStore, sample_interaction: Interaction) -> None:
    """Test saving and retrieving an interaction."""
    await store.save(sample_interaction)
    retrieved = await store.get(sample_interaction.id)

    assert retrieved is not None
    assert retrieved.id == sample_interaction.id
    assert retrieved.method == "POST"
    assert retrieved.path == "/v1/messages"
    assert retrieved.provider == Provider.ANTHROPIC
    assert retrieved.model == "claude-sonnet-4-20250514"
    assert retrieved.status_code == 200
    assert retrieved.response_text == "Hello! How can I help?"
    assert retrieved.is_streaming is False


@pytest.mark.asyncio
async def test_save_and_get_streaming(
    store: InteractionStore, sample_streaming_interaction: Interaction
) -> None:
    """Test saving and retrieving a streaming interaction."""
    await store.save(sample_streaming_interaction)
    retrieved = await store.get(sample_streaming_interaction.id)

    assert retrieved is not None
    assert retrieved.is_streaming is True
    assert len(retrieved.stream_chunks) == 2
    assert retrieved.stream_chunks[0].delta_text == "Hello"
    assert retrieved.stream_chunks[1].delta_text == "!"
    assert retrieved.time_to_first_token_ms == 120.0


@pytest.mark.asyncio
async def test_get_nonexistent(store: InteractionStore) -> None:
    """Test getting a nonexistent interaction returns None."""
    result = await store.get("nonexistent-id")
    assert result is None


@pytest.mark.asyncio
async def test_list_interactions(
    store: InteractionStore,
    sample_interaction: Interaction,
    sample_streaming_interaction: Interaction,
) -> None:
    """Test listing interactions."""
    await store.save(sample_interaction)
    await store.save(sample_streaming_interaction)

    results = await store.list_interactions()
    assert len(results) == 2


@pytest.mark.asyncio
async def test_list_interactions_filter_by_provider(
    store: InteractionStore,
    sample_interaction: Interaction,
    sample_streaming_interaction: Interaction,
) -> None:
    """Test listing interactions filtered by provider."""
    await store.save(sample_interaction)
    await store.save(sample_streaming_interaction)

    anthropic_results = await store.list_interactions(provider="anthropic")
    assert len(anthropic_results) == 1
    assert anthropic_results[0].provider == Provider.ANTHROPIC

    openai_results = await store.list_interactions(provider="openai")
    assert len(openai_results) == 1
    assert openai_results[0].provider == Provider.OPENAI


@pytest.mark.asyncio
async def test_list_interactions_filter_by_model(
    store: InteractionStore,
    sample_interaction: Interaction,
    sample_streaming_interaction: Interaction,
) -> None:
    """Test listing interactions filtered by model."""
    await store.save(sample_interaction)
    await store.save(sample_streaming_interaction)

    results = await store.list_interactions(model="gpt-4")
    assert len(results) == 1
    assert results[0].model == "gpt-4"


@pytest.mark.asyncio
async def test_list_interactions_with_limit(
    store: InteractionStore,
    sample_interaction: Interaction,
    sample_streaming_interaction: Interaction,
) -> None:
    """Test listing interactions with a limit."""
    await store.save(sample_interaction)
    await store.save(sample_streaming_interaction)

    results = await store.list_interactions(limit=1)
    assert len(results) == 1


@pytest.mark.asyncio
async def test_get_stats(
    store: InteractionStore,
    sample_interaction: Interaction,
    sample_streaming_interaction: Interaction,
) -> None:
    """Test getting aggregate statistics."""
    await store.save(sample_interaction)
    await store.save(sample_streaming_interaction)

    stats = await store.get_stats()
    assert stats["total_interactions"] == 2
    assert stats["by_provider"]["anthropic"] == 1
    assert stats["by_provider"]["openai"] == 1
    assert "claude-sonnet-4-20250514" in stats["by_model"]
    assert "gpt-4" in stats["by_model"]
    assert stats["avg_latency_ms"] is not None


@pytest.mark.asyncio
async def test_get_stats_empty(store: InteractionStore) -> None:
    """Test getting stats from an empty database."""
    stats = await store.get_stats()
    assert stats["total_interactions"] == 0
    assert stats["by_provider"] == {}
    assert stats["avg_latency_ms"] is None


@pytest.mark.asyncio
async def test_token_usage_roundtrip(
    store: InteractionStore, sample_interaction: Interaction
) -> None:
    """Test that token usage survives serialization roundtrip."""
    await store.save(sample_interaction)
    retrieved = await store.get(sample_interaction.id)

    assert retrieved is not None
    assert retrieved.token_usage is not None
    assert retrieved.token_usage.input_tokens == 10
    assert retrieved.token_usage.output_tokens == 15


@pytest.mark.asyncio
async def test_cost_estimate_roundtrip(
    store: InteractionStore, sample_interaction: Interaction
) -> None:
    """Test that cost estimate survives serialization roundtrip."""
    await store.save(sample_interaction)
    retrieved = await store.get(sample_interaction.id)

    assert retrieved is not None
    assert retrieved.cost_estimate is not None
    assert retrieved.cost_estimate.total_cost == pytest.approx(0.000105)


@pytest.mark.asyncio
async def test_request_headers_roundtrip(
    store: InteractionStore, sample_interaction: Interaction
) -> None:
    """Test that request headers survive serialization roundtrip."""
    await store.save(sample_interaction)
    retrieved = await store.get(sample_interaction.id)

    assert retrieved is not None
    assert retrieved.request_headers["content-type"] == "application/json"
    assert retrieved.request_headers["authorization"] == "Bearer sk-***"


@pytest.mark.asyncio
async def test_no_session_saved_as_orphan(store: InteractionStore) -> None:
    """Interaction without session_id or conversation_id is saved as an unlinked orphan."""
    interaction = Interaction(
        id="orphan-1",
        timestamp=datetime(2025, 2, 1, 12, 0, 0, tzinfo=UTC),
        method="POST",
        path="/v1/messages",
        provider=Provider.ANTHROPIC,
        model="claude-sonnet-4-20250514",
        messages=[{"role": "user", "content": "Hello"}],
        response_text="Hi there",
        status_code=200,
    )
    await store.save(interaction)
    retrieved = await store.get("orphan-1")

    assert retrieved is not None
    assert retrieved.conversation_id is None
    assert retrieved.parent_interaction_id is None
    assert retrieved.turn_number is None
    assert retrieved.turn_type is None


@pytest.mark.asyncio
async def test_malformed_row_skipped_in_list(
    store: InteractionStore, sample_interaction: Interaction
) -> None:
    """A malformed DB row should be skipped without crashing list_interactions."""
    await store.save(sample_interaction)

    # Corrupt the request_headers column of the saved row to invalid JSON
    await store.db.execute(
        "UPDATE interactions SET request_headers = 'not-valid-json' WHERE id = ?",
        (sample_interaction.id,),
    )
    await store.db.commit()

    # Should return an empty list instead of raising
    results = await store.list_interactions()
    assert results == []


@pytest.mark.asyncio
async def test_malformed_row_does_not_affect_other_rows(
    store: InteractionStore,
    sample_interaction: Interaction,
    sample_streaming_interaction: Interaction,
) -> None:
    """A malformed row should be skipped; valid rows in the same query are still returned."""
    await store.save(sample_interaction)
    await store.save(sample_streaming_interaction)

    # Corrupt only the first interaction
    await store.db.execute(
        "UPDATE interactions SET request_headers = 'not-valid-json' WHERE id = ?",
        (sample_interaction.id,),
    )
    await store.db.commit()

    results = await store.list_interactions()
    assert len(results) == 1
    assert results[0].id == sample_streaming_interaction.id


@pytest.mark.asyncio
async def test_no_session_does_not_link_to_previous(store: InteractionStore) -> None:
    """Two interactions without session_id should NOT be linked via global search."""
    first = Interaction(
        id="nosession-1",
        timestamp=datetime(2025, 2, 1, 12, 0, 0, tzinfo=UTC),
        method="POST",
        path="/v1/messages",
        provider=Provider.ANTHROPIC,
        model="claude-sonnet-4-20250514",
        messages=[{"role": "user", "content": "First message"}],
        response_text="First response",
        status_code=200,
    )
    second = Interaction(
        id="nosession-2",
        timestamp=datetime(2025, 2, 1, 12, 0, 1, tzinfo=UTC),
        method="POST",
        path="/v1/messages",
        provider=Provider.ANTHROPIC,
        model="claude-sonnet-4-20250514",
        messages=[
            {"role": "user", "content": "First message"},
            {"role": "assistant", "content": "First response"},
            {"role": "user", "content": "Second message"},
        ],
        status_code=200,
    )
    await store.save(first)
    await store.save(second)

    retrieved_first = await store.get("nosession-1")
    retrieved_second = await store.get("nosession-2")

    assert retrieved_first is not None
    assert retrieved_second is not None
    # Neither should have threading metadata
    assert retrieved_first.conversation_id is None
    assert retrieved_second.conversation_id is None
    assert retrieved_second.parent_interaction_id is None


@pytest.mark.asyncio
async def test_agent_graph_single_agent(store: InteractionStore) -> None:
    """Two interactions with the same session_id → 1 node, 0 edges."""
    i1 = Interaction(
        id="ag-single-1",
        session_id="session-A",
        conversation_id="conv-single",
        method="POST",
        path="/v1/messages",
        provider=Provider.ANTHROPIC,
    )
    i2 = Interaction(
        id="ag-single-2",
        session_id="session-A",
        conversation_id="conv-single",
        method="POST",
        path="/v1/messages",
        provider=Provider.ANTHROPIC,
    )
    await store.save(i1)
    await store.save(i2)

    graph = await store.get_agent_graph("conv-single")
    assert isinstance(graph, AgentGraph)
    assert len(graph.nodes) == 1
    assert graph.nodes[0].session_id == "session-A"
    assert len(graph.edges) == 0


@pytest.mark.asyncio
async def test_agent_graph_handoff(store: InteractionStore) -> None:
    """Two interactions with different session_ids and a handoff → 2 nodes, 1 edge."""
    conv_id = "conv-handoff"
    i1 = Interaction(
        id="ag-handoff-1",
        session_id="session-A",
        conversation_id=conv_id,
        method="POST",
        path="/v1/messages",
        provider=Provider.ANTHROPIC,
    )
    await store.save(i1)

    # Second interaction in a different session — threading detects handoff
    i2 = Interaction(
        id="ag-handoff-2",
        session_id="session-B",
        conversation_id=conv_id,
        method="POST",
        path="/v1/messages",
        provider=Provider.ANTHROPIC,
    )
    await store.save(i2)

    graph = await store.get_agent_graph(conv_id)
    assert isinstance(graph, AgentGraph)
    assert len(graph.nodes) == 2
    assert len(graph.edges) == 1
    edge = graph.edges[0]
    assert edge.from_session_id == "session-A"
    assert edge.to_session_id == "session-B"


@pytest.mark.asyncio
async def test_agent_graph_not_found(store: InteractionStore) -> None:
    """get_agent_graph for a nonexistent conversation → empty graph, no exception."""
    graph = await store.get_agent_graph("nonexistent-conv")
    assert isinstance(graph, AgentGraph)
    assert graph.nodes == []
    assert graph.edges == []
