"""Failure isolation: a failing side effect must not break feedback processing.

`FeedbackManager` runs every independently failing side effect of feedback
processing (correlation, provenance, routing, each handler, each subscriber)
through `FailurePolicy.run_stage`, which applies the `FailureMode` configured
for that stage. Persistence is not a stage: store failures always propagate as
`FeedbackStoreError`, because feedback that was not stored cannot be processed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING

from feedback_manager import _logging
from feedback_manager.errors import (
    FeedbackConfigurationError,
    FeedbackCorrelationError,
    FeedbackHandlerError,
    FeedbackManagerError,
    FeedbackRoutingError,
    FeedbackSubscriberError,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping
    from uuid import UUID

_logger = _logging.get_logger("feedback_manager.policies.failure")


class FailureMode(StrEnum):
    """How a failure in one stage is handled."""

    BEST_EFFORT = "best_effort"
    """Log the failure and continue without the stage's result."""
    BLOCKING = "blocking"
    """Raise the stage's error type, with the failure chained as ``__cause__``."""


class FeedbackStage(StrEnum):
    """The processing stages whose failures can be isolated."""

    CORRELATION = "correlation"
    """Assigning the correlation ID; best-effort falls back to the default derivation."""
    PROVENANCE = "provenance"
    """Resolving ``langgraph-xai`` provenance; best-effort stores the event without it."""
    ROUTING = "routing"
    """Selecting handlers; best-effort runs no handlers."""
    HANDLER = "handler"
    """Running one handler; best-effort skips to the next handler."""
    SUBSCRIBER = "subscriber"
    """Notifying one subscriber; best-effort skips to the next subscriber."""


_STAGE_ERRORS: Mapping[FeedbackStage, type[FeedbackManagerError]] = MappingProxyType(
    {
        FeedbackStage.CORRELATION: FeedbackCorrelationError,
        FeedbackStage.PROVENANCE: FeedbackCorrelationError,
        FeedbackStage.ROUTING: FeedbackRoutingError,
        FeedbackStage.HANDLER: FeedbackHandlerError,
        FeedbackStage.SUBSCRIBER: FeedbackSubscriberError,
    }
)


@dataclass(frozen=True, slots=True)
class FailurePolicy:
    """The `FailureMode` of each `FeedbackStage`.

    Every stage defaults to ``BEST_EFFORT``; ``modes`` overrides individual
    stages and keeps the default for the rest. Stages and modes may be given as
    enum members or their string values.

    Example:
        ```python
        FailurePolicy(modes={FeedbackStage.HANDLER: FailureMode.BLOCKING})
        ```

    Raises:
        FeedbackConfigurationError: If ``modes`` names an unknown stage or mode.
    """

    modes: Mapping[FeedbackStage, FailureMode] = field(default_factory=dict)

    def __post_init__(self) -> None:
        merged = dict.fromkeys(FeedbackStage, FailureMode.BEST_EFFORT)
        for stage, mode in self.modes.items():
            try:
                merged[FeedbackStage(stage)] = FailureMode(mode)
            except ValueError:
                raise FeedbackConfigurationError(
                    f"invalid failure policy entry {stage!r}: {mode!r}"
                ) from None
        object.__setattr__(self, "modes", MappingProxyType(merged))

    def mode_for(self, stage: FeedbackStage) -> FailureMode:
        """Return the failure mode configured for ``stage``."""
        return self.modes[stage]

    async def run_stage[T](
        self,
        stage: FeedbackStage,
        operation: Callable[[], Awaitable[T]],
        *,
        feedback_id: UUID | None = None,
        on_error: Callable[[FeedbackStage, Exception], None] | None = None,
    ) -> T | None:
        """Run ``operation`` and apply the failure mode of ``stage`` if it raises.

        Args:
            stage: The stage ``operation`` belongs to.
            operation: The work to run.
            feedback_id: The feedback event being processed, for error context.
            on_error: Called with the stage and the exception before the
                failure mode is applied, in both modes.

        Returns:
            The operation's result, or ``None`` after a best-effort failure.

        Raises:
            FeedbackManagerError: The stage's error type (for example
                `FeedbackHandlerError`) when the stage is ``BLOCKING``.
        """
        try:
            return await operation()
        except Exception as exc:
            if on_error is not None:
                on_error(stage, exc)
            if self.modes[stage] is FailureMode.BLOCKING:
                raise _STAGE_ERRORS[stage](
                    f"{stage} stage failed: {type(exc).__name__}: {exc}",
                    feedback_id=feedback_id,
                    stage=stage.value,
                ) from exc
            _logger.warning(
                "feedback stage failed; continuing",
                stage=stage.value,
                feedback_id=None if feedback_id is None else str(feedback_id),
                error_type=type(exc).__name__,
                exc_info=exc,
            )
            return None


__all__ = ["FailureMode", "FailurePolicy", "FeedbackStage"]
