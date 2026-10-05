"""Recording LangChain and LangGraph failures as feedback through LangChain callbacks."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from langchain_core.callbacks import AsyncCallbackHandler

from feedback_manager.core.sources import FeedbackSource
from feedback_manager.core.targets import FeedbackTarget, FeedbackTargetType
from feedback_manager.integrations._values import text_or_none
from feedback_manager.integrations.langchain.failures import (
    category_for_error,
    caused_by,
    failed_node,
    is_reportable,
)
from feedback_manager.integrations.langgraph.adapter import execution_context_from_config

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from uuid import UUID

    from langchain_core.documents import Document
    from langchain_core.messages import BaseMessage
    from langchain_core.outputs import LLMResult

    from feedback_manager.api.manager import FeedbackManager

type _Kind = Literal["tool", "model", "retriever", "chain"]

_MAX_TREES = 10_000
"""The most top-level runs tracked at once.

LangChain reports no end for a cancelled top-level tool, model, or retriever
call, so the oldest top-level runs are forgotten beyond this many.
"""


@dataclass(frozen=True, slots=True)
class _Run:
    root: UUID
    name: str | None
    metadata: Mapping[str, Any]
    tool_call_id: str | None


class FeedbackCallbackHandler(AsyncCallbackHandler):
    """Records each tool, model, retriever, and chain failure as one feedback event.

    Attach it to a run like any LangChain callback handler; it is inherited by
    every nested runnable, including LangGraph nodes and tools:

    ```python
    handler = FeedbackCallbackHandler(manager)
    await graph.ainvoke(inputs, config={"callbacks": [handler]})
    ```

    LangChain reports a failure to every enclosing run as it propagates. The
    handler records it once, where it happened: a failing tool produces one
    ``TOOL`` event for its tool call, not one more per enclosing node and graph.
    Events carry the execution context of the failing run (thread, node, tool
    call, and, inside a ``langgraph-xai`` instrumented graph, the run ID used
    for provenance). LangGraph control flow, such as human-in-the-loop
    interrupts, is not a failure and is not recorded.

    Cancelling a run cancels every run inside it, and LangGraph cancels the
    other running nodes when one fails. Those cancellations are a consequence,
    so a cancelled run is recorded once, about the top-level run, and a node
    failure is recorded without the cancellations it caused. A LangGraph node
    timeout, or a node that cancels itself, is recorded about that node.

    The handler remembers each run's context from its start callback until it
    ends, for at most 10,000 top-level runs at once: LangChain reports no end
    for a cancelled top-level tool or model call, so the oldest are forgotten
    beyond that.

    Events are ``FAILURE``, ``TIMEOUT``, or ``CANCELLATION``
    (`category_for_error`) with ``feedback_type`` ``"tool_error"``,
    ``"model_error"``, ``"retriever_error"``, or ``"chain_error"``, and a
    payload with ``error``, ``error_type``, and ``operation`` (the name of the
    failing run). Success is not reported: whether an outcome deserves
    feedback is the application's decision.
    """

    def __init__(self, manager: FeedbackManager) -> None:
        self._manager = manager
        self._lock = threading.Lock()
        self._runs: dict[UUID, _Run] = {}
        # The runs started under each top-level run, so it can forget them all.
        self._trees: dict[UUID, set[UUID]] = {}
        self._reported: dict[UUID, list[BaseException]] = {}

    # -- run starts: remember each run's context --------------------------------

    async def on_chain_start(
        self,
        serialized: dict[str, Any],
        inputs: dict[str, Any],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Remember the chain run's context."""
        self._start(run_id, parent_run_id, metadata, _name(serialized, kwargs))

    async def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        inputs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Remember the tool run's context and tool call ID."""
        self._start(
            run_id,
            parent_run_id,
            metadata,
            _name(serialized, kwargs),
            text_or_none(kwargs.get("tool_call_id")),
        )

    async def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Remember the model run's context."""
        self._start(run_id, parent_run_id, metadata, _name(serialized, kwargs))

    async def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[BaseMessage]],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Remember the chat model run's context."""
        self._start(run_id, parent_run_id, metadata, _name(serialized, kwargs))

    async def on_retriever_start(
        self,
        serialized: dict[str, Any],
        query: str,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Remember the retriever run's context."""
        self._start(run_id, parent_run_id, metadata, _name(serialized, kwargs))

    # -- run ends: forget finished runs -------------------------------------------

    async def on_chain_end(
        self,
        outputs: dict[str, Any],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """Forget the finished chain run."""
        self._finish(run_id)

    async def on_tool_end(
        self,
        output: Any,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """Forget the finished tool run."""
        self._finish(run_id)

    async def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """Forget the finished model run."""
        self._finish(run_id)

    async def on_retriever_end(
        self,
        documents: Sequence[Document],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """Forget the finished retriever run."""
        self._finish(run_id)

    # -- failures: record each one once -------------------------------------------

    async def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """Record a tool failure as ``TOOL`` feedback about the tool call."""
        await self._failed("tool", error, run_id, parent_run_id, kwargs)

    async def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """Record a model failure as ``GENERATION`` feedback about the generation."""
        await self._failed("model", error, run_id, parent_run_id, kwargs)

    async def on_retriever_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """Record a retriever failure as ``TOOL`` feedback about the retriever run."""
        await self._failed("retriever", error, run_id, parent_run_id, kwargs)

    async def on_chain_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """Record a failure that originated in a chain or graph node as ``AGENT`` feedback."""
        await self._failed("chain", error, run_id, parent_run_id, kwargs)

    # -- internals ---------------------------------------------------------------

    def _start(
        self,
        run_id: UUID,
        parent_run_id: UUID | None,
        metadata: Mapping[str, Any] | None,
        name: str | None,
        tool_call_id: str | None = None,
    ) -> None:
        with self._lock:
            parent = None if parent_run_id is None else self._runs.get(parent_run_id)
            root = run_id if parent is None else parent.root
            self._runs[run_id] = _Run(root, name, dict(metadata or {}), tool_call_id)
            tree = self._trees.get(root)
            if tree is None:
                if len(self._trees) >= _MAX_TREES:
                    oldest = next(iter(self._trees))
                    self._forget(oldest, oldest)
                tree = self._trees[root] = set()
            tree.add(run_id)

    def _finish(self, run_id: UUID) -> None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is not None:
                self._forget(run_id, run.root)

    def _forget(self, run_id: UUID, root: UUID) -> None:
        # Called with the lock held. LangChain reports no end for a cancelled
        # tool or model call, so a finished root also forgets every run left in
        # its tree.
        if run_id == root:
            for member in self._trees.pop(root, ()):
                self._runs.pop(member, None)
            self._reported.pop(root, None)
            return
        self._runs.pop(run_id, None)
        self._trees[root].discard(run_id)

    async def _failed(
        self,
        kind: _Kind,
        error: BaseException,
        run_id: UUID,
        parent_run_id: UUID | None,
        details: Mapping[str, Any],
    ) -> None:
        with self._lock:
            run = self._runs.get(run_id)
            parent = None if parent_run_id is None else self._runs.get(parent_run_id)
            root = run.root if run is not None else (run_id if parent is None else parent.root)
            # Below the top-level run, or reported after the run around it finished.
            inside = root != run_id or (run is None and parent_run_id is not None)
            reported = self._reported.setdefault(root, [])
            record = (
                is_reportable(error)
                and not (inside and isinstance(error, asyncio.CancelledError))
                and not any(caused_by(error, known) for known in reported)
            )
            if record:
                reported.append(error)
            self._forget(run_id, root)
        if record:
            await self._manager.submit(**_feedback(kind, error, run_id, run, details))


def _feedback(
    kind: _Kind,
    error: BaseException,
    run_id: UUID,
    run: _Run | None,
    details: Mapping[str, Any],
) -> dict[str, Any]:
    metadata: Mapping[str, Any] = {} if run is None else run.metadata
    name = None if run is None else run.name
    config = {"metadata": metadata}
    fields: dict[str, Any] = {
        "category": category_for_error(error),
        "feedback_type": f"{kind}_error",
        "payload": {"error": str(error), "error_type": type(error).__name__, "operation": name},
    }
    if kind == "tool":
        tool_call_id = (
            text_or_none(details.get("tool_call_id"))
            or (None if run is None else run.tool_call_id)
            or str(run_id)
        )
        fields["source"] = FeedbackSource.TOOL
        fields["target"] = FeedbackTarget(type=FeedbackTargetType.TOOL_CALL, id=tool_call_id)
        fields["execution_context"] = execution_context_from_config(
            config, tool_call_id=tool_call_id
        )
    elif kind == "model":
        fields["source"] = FeedbackSource.GENERATION
        fields["target"] = FeedbackTarget(type=FeedbackTargetType.GENERATION, id=str(run_id))
        fields["execution_context"] = execution_context_from_config(
            config, generation_id=str(run_id)
        )
    elif kind == "retriever":
        fields["source"] = FeedbackSource.TOOL
        fields["target"] = FeedbackTarget(type=FeedbackTargetType.RUN, id=str(run_id))
        fields["execution_context"] = execution_context_from_config(config)
    else:
        node = text_or_none(failed_node(error))
        if node is not None:
            # The graph reports a node timeout, or a cancellation the node raised.
            fields["payload"]["operation"] = node
        elif text_or_none(metadata.get("langgraph_node")) == name:
            node = name
        fields["source"] = FeedbackSource.AGENT
        fields["target"] = (
            FeedbackTarget(type=FeedbackTargetType.RUN, id=str(run_id))
            if node is None
            else FeedbackTarget(type=FeedbackTargetType.NODE, id=node)
        )
        fields["execution_context"] = execution_context_from_config(config, node_id=node)
    return fields


def _name(serialized: Mapping[str, Any] | None, details: Mapping[str, Any]) -> str | None:
    return text_or_none(details.get("name")) or text_or_none((serialized or {}).get("name"))


__all__ = ["FeedbackCallbackHandler"]
