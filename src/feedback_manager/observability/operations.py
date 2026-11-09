"""Optional, framework-neutral operation timing hooks."""

from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import AbstractContextManager, nullcontext


class OperationObserver(ABC):
    """Provide synchronous span scopes that remain open across awaited operations."""

    @abstractmethod
    def observe(self, name: str) -> AbstractContextManager[None]:
        """Observe one library operation without suppressing its failures."""


def operation(observer: OperationObserver | None, name: str) -> AbstractContextManager[None]:
    """Select a configured observer, or a zero-export context."""
    return nullcontext() if observer is None else observer.observe(name)


__all__ = ["OperationObserver", "operation"]
