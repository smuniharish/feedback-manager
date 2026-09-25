"""Helpers for correlating LangGraph's own streaming output with feedback.

FeedbackManager does not implement a streaming engine -- it uses
``graph.astream(...)`` exactly as LangGraph provides it. This module only
recognizes the ``__interrupt__`` marker LangGraph emits (with
``stream_mode="updates"``) so callers can react to a pause with
:class:`~feedback_manager.integrations.langgraph.interrupt.HumanInTheLoopBridge`
without hand-rolling that detection themselves.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from langgraph.types import Interrupt

INTERRUPT_KEY = "__interrupt__"


def extract_interrupts(chunk: Mapping[str, Any]) -> tuple[Interrupt, ...]:
    """Return any ``Interrupt`` objects carried by a LangGraph stream chunk.

    Returns an empty tuple for chunks that are not interruptions.
    """
    payload: Iterable[Any] | None = chunk.get(INTERRUPT_KEY)
    if not payload:
        return ()
    return tuple(item for item in payload if isinstance(item, Interrupt))


__all__ = ["INTERRUPT_KEY", "extract_interrupts"]
