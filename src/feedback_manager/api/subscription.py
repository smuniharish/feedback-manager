"""The handle returned by `FeedbackManager.subscribe`."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import TracebackType


class Subscription:
    """Stops notifications to one subscriber when cancelled.

    It is also a context manager that cancels itself on exit:

    ```python
    with manager.subscribe(on_feedback):
        await run_workflow()
    ```
    """

    __slots__ = ("_active", "_cancel")

    def __init__(self, cancel: Callable[[], None]) -> None:
        self._cancel = cancel
        self._active = True

    @property
    def active(self) -> bool:
        """Whether the subscriber still receives notifications."""
        return self._active

    def cancel(self) -> None:
        """Stop notifying the subscriber; calling it again has no effect."""
        if self._active:
            self._active = False
            self._cancel()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.cancel()


__all__ = ["Subscription"]
