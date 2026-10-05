"""The async iterator returned by `FeedbackManager.stream`."""

from __future__ import annotations

import asyncio
import enum
from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import TracebackType

    from feedback_manager.contracts.store import FeedbackQuery
    from feedback_manager.core.events import FeedbackEvent


class _Signal(enum.Enum):
    CLOSED = enum.auto()


class FeedbackStream:
    """Yields feedback events published after the stream was opened.

    Every newly received event and every lifecycle change matching the
    stream's query is delivered, in publication order. Events are published
    from whichever task, thread, or event loop changes the feedback, and are
    handed to the event loop that opened the stream; consume the stream on
    that loop. Changes made concurrently to one event can be published in a
    different order than they were applied; their ``updated_at`` gives the
    order they were applied in. Close the stream with `aclose`, or use it as an
    async context manager:

    ```python
    async with manager.stream(FeedbackQuery(category="correction")) as stream:
        async for event in stream:
            print(event.feedback_id, event.status)
    ```

    Closing a stream, or calling `FeedbackManager.aclose`, ends iteration after
    the events already delivered to it. Undelivered events are buffered without
    limit, so close streams you no longer read.
    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        query: FeedbackQuery | None,
        on_close: Callable[[FeedbackStream], None],
    ) -> None:
        self._loop = loop
        self._query = query
        self._on_close = on_close
        self._queue: asyncio.Queue[FeedbackEvent | _Signal] = asyncio.Queue()
        self._closed = False
        self._finished = False

    @property
    def closed(self) -> bool:
        """Whether the stream was closed; iteration ends after the delivered events."""
        return self._closed

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> FeedbackEvent:
        if self._finished:
            raise StopAsyncIteration
        item = await self._queue.get()
        if isinstance(item, _Signal):
            self._finished = True
            raise StopAsyncIteration
        return item

    async def aclose(self) -> None:
        """Close the stream; iteration ends after the events already delivered."""
        self._close()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._close()

    def _publish(self, event: FeedbackEvent) -> None:
        if not self._closed and (self._query is None or self._query.matches(event)):
            self._deliver(event)

    def _close(self) -> None:
        if not self._closed:
            self._closed = True
            self._on_close(self)
            self._deliver(_Signal.CLOSED)

    def _deliver(self, item: FeedbackEvent | _Signal) -> None:
        # Both paths append to the loop's ready queue, so items reach the stream
        # in the order they were published, whichever thread published them.
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self._loop:
            self._loop.call_soon(self._queue.put_nowait, item)
            return
        try:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, item)
        except RuntimeError:
            # The consuming event loop is closed, so nobody can read the stream anymore.
            self._closed = True
            self._on_close(self)


__all__ = ["FeedbackStream"]
