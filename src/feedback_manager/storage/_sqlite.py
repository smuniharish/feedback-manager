"""Shared single-connection SQLite lifecycle for local archives."""

from __future__ import annotations

import asyncio
import os
import sqlite3
import threading
from contextlib import ExitStack
from typing import TYPE_CHECKING

from feedback_manager.learning.errors import LearningError, LearningValidationError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path


class SQLiteHandle:
    """Serialize access to a local connection and offload operations to threads."""

    def __init__(
        self,
        path: str | Path,
        schema: str,
        *,
        name: str,
        error_type: type[LearningError],
    ) -> None:
        filename = os.fspath(path)
        if not filename or filename == ":memory:" or filename.startswith("file:"):
            raise LearningValidationError(f"{name} archive requires a local database file")
        self._lock = threading.Lock()
        self._error_type = error_type
        self._name = name
        with ExitStack() as stack:
            self.connection = sqlite3.connect(filename, check_same_thread=False, timeout=30)
            stack.callback(self.connection.close)
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.executescript(schema)
            self.connection.commit()
            stack.pop_all()
        self._closed = False

    async def run[R](self, operation: Callable[[], R]) -> R:
        """Run and commit one operation, or roll back on failure."""

        def locked() -> R:
            with self._lock:
                if self._closed:
                    raise self._error_type(f"{self._name} repository is closed")
                with self.connection:
                    return operation()

        return await asyncio.to_thread(locked)

    async def close(self) -> None:
        """Wait for an in-flight operation and close idempotently."""

        def close() -> None:
            with self._lock:
                if not self._closed:
                    self.connection.close()
                    self._closed = True

        await asyncio.to_thread(close)


async def open_with_cleanup[T](
    factory: Callable[[], T], close: Callable[[T], Awaitable[None]]
) -> T:
    """Finish and close a constructed resource before propagating cancellation."""
    opening = asyncio.create_task(asyncio.to_thread(factory))
    try:
        return await asyncio.shield(opening)
    except asyncio.CancelledError as cancelled:
        while not opening.done():
            try:
                await asyncio.shield(opening)
            except asyncio.CancelledError:
                continue
        resource = opening.result()
        closing = asyncio.ensure_future(close(resource))
        while not closing.done():
            try:
                await asyncio.shield(closing)
            except asyncio.CancelledError:
                continue
        closing.result()
        raise cancelled
