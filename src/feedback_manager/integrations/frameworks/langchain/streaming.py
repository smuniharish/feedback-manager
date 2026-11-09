"""Finding interrupts in LangGraph results and stream chunks."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langgraph.types import Interrupt

if TYPE_CHECKING:
    from collections.abc import Mapping

INTERRUPT_KEY = "__interrupt__"
"""The key under which LangGraph reports pending interrupts."""


def extract_interrupts(chunk: Mapping[str, Any]) -> tuple[Interrupt, ...]:
    """Return the interrupts in a LangGraph result or ``stream_mode="updates"`` chunk.

    Works with the value ``invoke``/``ainvoke`` return for a paused graph and
    with the chunks of ``stream``/``astream``. Returns an empty tuple when the
    graph did not pause.
    """
    pending = chunk.get(INTERRUPT_KEY) or ()
    return tuple(item for item in pending if isinstance(item, Interrupt))


__all__ = ["INTERRUPT_KEY", "extract_interrupts"]
