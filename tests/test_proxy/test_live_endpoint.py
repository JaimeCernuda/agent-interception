"""Tests for the live-tail broadcaster and the server-side fanout wrapper.

The `/_interceptor/live` SSE endpoint itself is a thin adapter around
`InteractionBroadcaster.subscribe()` plus SSE framing; it is covered by
manual end-to-end verification rather than a flaky httpx-ASGITransport
streaming test.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from agent_interception.models import Interaction, Provider
from agent_interception.proxy.broadcaster import InteractionBroadcaster
from agent_interception.proxy.server import _make_fanout


def _make_interaction(interaction_id: str = "itx-1") -> Interaction:
    return Interaction(
        id=interaction_id,
        timestamp=datetime(2026, 4, 22, 12, 0, 0, tzinfo=UTC),
        method="POST",
        path="/v1/messages",
        provider=Provider.ANTHROPIC,
        model="claude-sonnet-4-5",
        status_code=200,
        is_streaming=False,
        response_text="hi",
        total_latency_ms=10.0,
    )


class TestBroadcasterUnit:
    async def test_delivers_to_single_subscriber(self) -> None:
        bus = InteractionBroadcaster()
        queue = bus.subscribe()
        await bus.publish(_make_interaction())
        received = await asyncio.wait_for(queue.get(), timeout=0.5)
        assert received.id == "itx-1"

    async def test_delivers_to_multiple_subscribers(self) -> None:
        bus = InteractionBroadcaster()
        q1 = bus.subscribe()
        q2 = bus.subscribe()
        await bus.publish(_make_interaction("a"))
        r1 = await asyncio.wait_for(q1.get(), timeout=0.5)
        r2 = await asyncio.wait_for(q2.get(), timeout=0.5)
        assert r1.id == "a"
        assert r2.id == "a"

    async def test_unsubscribe_stops_delivery(self) -> None:
        bus = InteractionBroadcaster()
        queue = bus.subscribe()
        bus.unsubscribe(queue)
        await bus.publish(_make_interaction())
        assert queue.empty()
        assert bus.subscriber_count == 0

    async def test_drops_oldest_when_full(self) -> None:
        bus = InteractionBroadcaster(queue_size=2)
        queue = bus.subscribe()
        for idx in range(5):
            await bus.publish(_make_interaction(f"itx-{idx}"))
        assert queue.qsize() == 2
        first = queue.get_nowait()
        second = queue.get_nowait()
        assert (first.id, second.id) == ("itx-3", "itx-4")


class TestFanout:
    async def test_publishes_even_when_user_callback_raises(self) -> None:
        """A broken user callback must not starve SSE subscribers."""
        bus = InteractionBroadcaster()
        queue = bus.subscribe()

        async def bad(interaction: Interaction) -> None:
            raise RuntimeError("user callback is broken")

        fanout = _make_fanout(bus, bad)
        await fanout(_make_interaction("safe-1"))

        received = await asyncio.wait_for(queue.get(), timeout=0.5)
        assert received.id == "safe-1"

    async def test_invokes_user_callback_when_provided(self) -> None:
        bus = InteractionBroadcaster()
        seen: list[str] = []

        async def capture(interaction: Interaction) -> None:
            seen.append(interaction.id)

        fanout = _make_fanout(bus, capture)
        await fanout(_make_interaction("cb-1"))
        assert seen == ["cb-1"]

    async def test_noop_when_no_user_callback(self) -> None:
        bus = InteractionBroadcaster()
        queue = bus.subscribe()
        fanout = _make_fanout(bus, None)
        await fanout(_make_interaction("solo"))
        received = await asyncio.wait_for(queue.get(), timeout=0.5)
        assert received.id == "solo"
