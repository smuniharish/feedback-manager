"""A dependency-free adapter for custom Python agents.

Use it for agent loops you write yourself, for multi-agent systems that pass
plain dictionaries between agents, and for frameworks that have no dedicated
adapter yet. It implements every contract in `feedback_manager.integrations.base`
with plain Python types:

- the execution context is read from a mapping whose keys are
  `ExecutionContext` field names;
- a pause is a `PendingInterrupt`, and resuming produces a `ResumeValue`;
- learned context is rendered as text by `TextContextInjector`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from feedback_manager.core.context import ExecutionContext
from feedback_manager.integrations._values import text_or_none
from feedback_manager.integrations.base import (
    ExecutionContextExtractor,
    FrameworkAdapter,
    HumanInTheLoopBridge,
    TextContextInjector,
)

if TYPE_CHECKING:
    from feedback_manager.api.manager import FeedbackManager
    from feedback_manager.integrations.base import LearnedArtifactRetriever

_IDENTIFIERS = tuple(name for name in ExecutionContext.model_fields if name != "metadata")


class CustomContextExtractor(ExecutionContextExtractor[Mapping[str, Any]]):
    """Reads an `ExecutionContext` from a mapping keyed by its field names.

    Keys that are not identifiers of `ExecutionContext` are ignored, values
    are converted to strings, and blank values are treated as missing.
    Explicit keyword arguments override the mapping.
    """

    def extract(
        self,
        source: Mapping[str, Any] | None,
        *,
        node_id: str | None = None,
        tool_call_id: str | None = None,
        generation_id: str | None = None,
        message_id: str | None = None,
        interrupt_id: str | None = None,
    ) -> ExecutionContext:
        """Return the execution context described by ``source``."""
        source = source or {}
        values = {name: text_or_none(source.get(name)) for name in _IDENTIFIERS}
        overrides = {
            "node_id": node_id,
            "tool_call_id": tool_call_id,
            "generation_id": generation_id,
            "message_id": message_id,
            "interrupt_id": interrupt_id,
        }
        for name, value in overrides.items():
            if (text := text_or_none(value)) is not None:
                values[name] = text
        return ExecutionContext.model_validate(values)


@dataclass(frozen=True, slots=True)
class PendingInterrupt:
    """A pause in a custom agent: a stable ID and what the person is asked."""

    id: str
    value: Any


@dataclass(frozen=True, slots=True)
class ResumeValue:
    """What a custom agent receives to continue after a pause."""

    response: Any
    interrupt_id: str | None = None


class CustomHumanInTheLoopBridge(HumanInTheLoopBridge[PendingInterrupt, ResumeValue]):
    """Records `PendingInterrupt` pauses and builds `ResumeValue` answers."""

    def interrupt_id(self, interrupt: PendingInterrupt) -> str:
        """Return the interrupt's ID."""
        return interrupt.id

    def interrupt_value(self, interrupt: PendingInterrupt) -> Any:
        """Return what the interrupt asks."""
        return interrupt.value

    def resume_command(self, response: Any, *, interrupt_id: str | None = None) -> ResumeValue:
        """Return a `ResumeValue` carrying ``response``."""
        return ResumeValue(response=response, interrupt_id=interrupt_id)


class CustomAgentAdapter(FrameworkAdapter):
    """The `FrameworkAdapter` for custom Python agents; needs no extra dependency."""

    name: ClassVar[str] = "custom"

    def context_extractor(self) -> CustomContextExtractor:
        """Return a `CustomContextExtractor`."""
        return CustomContextExtractor()

    def human_in_the_loop(self, manager: FeedbackManager) -> CustomHumanInTheLoopBridge:
        """Return a `CustomHumanInTheLoopBridge` recording with ``manager``."""
        return CustomHumanInTheLoopBridge(manager)

    def context_injector(self, retriever: LearnedArtifactRetriever) -> TextContextInjector:
        """Return a `TextContextInjector` retrieving through ``retriever``."""
        return TextContextInjector(retriever)


__all__ = [
    "CustomAgentAdapter",
    "CustomContextExtractor",
    "CustomHumanInTheLoopBridge",
    "PendingInterrupt",
    "ResumeValue",
]
