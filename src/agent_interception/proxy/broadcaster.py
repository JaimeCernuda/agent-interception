"""In-process fan-out of newly-persisted interactions to live subscribers.

The broadcaster is a tiny asyncio pub/sub: callers (the SSE endpoint)
`subscribe()` to get a queue, drain it with `queue.get()`, and must
`unsubscribe(queue)` when done. Queues are bounded; if a subscriber falls
behind, the oldest event is dropped so `publish` never blocks the request
path — observability must not slow the proxy.
"""

from __future__ import annotations

import asyncio

from agent_interception.models import Interaction

_DEFAULT_QUEUE_SIZE = 256


class InteractionBroadcaster:
    """Fan out `Interaction` events to any number of async subscribers."""

    def __init__(self, *, queue_size: int = _DEFAULT_QUEUE_SIZE) -> None:
        self._queue_size = queue_size
        self._subscribers: set[asyncio.Queue[Interaction]] = set()

    async def publish(self, interaction: Interaction) -> None:
        """Push an interaction to every subscriber without blocking.

        If a subscriber's queue is full, the oldest queued item is discarded
        to make room.
        """
        for queue in list(self._subscribers):
            while True:
                try:
                    queue.put_nowait(interaction)
                    break
                except asyncio.QueueFull:
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break

    def subscribe(self) -> asyncio.Queue[Interaction]:
        """Register a new subscriber and return its event queue."""
        queue: asyncio.Queue[Interaction] = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Interaction]) -> None:
        """Stop delivering events to the given subscriber."""
        self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)
