"""Bring your own agxai ProvenanceStore: runtime-registered or passed to the provider."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypedDict

import pytest
from agxai import InMemoryProvenanceStore, ProvenanceStore, XAIRuntime
from agxai.testing import ProvenanceStoreContract
from langgraph.graph import END, START, StateGraph

from feedback_manager import (
    ExecutionContext,
    FeedbackConfigurationError,
    FeedbackManager,
    FeedbackTarget,
)
from feedback_manager.integrations.provenance import AgxaiProvenanceProvider

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from agxai import Execution, ProvenanceLink
    from agxai import ExecutionContext as XaiContext
    from agxai.core import StoreQuery
    from agxai.core.models import CanonicalEvent

    type Item = Execution | ProvenanceLink | CanonicalEvent

pytestmark = pytest.mark.integration


class AuditedStore(ProvenanceStore):
    """An application-owned store: records every read and delegates persistence."""

    def __init__(self) -> None:
        self._records = InMemoryProvenanceStore()
        self.queries = 0

    async def write(self, item: Item) -> None:
        await self._records.write(item)

    async def get(self, entity_id: str, *, context: XaiContext) -> Item | None:
        return await self._records.get(entity_id, context=context)

    async def query(self, query: StoreQuery) -> AsyncIterator[Item]:
        self.queries += 1
        async for item in self._records.query(query):
            yield item

    async def parents(self, entity_id: str, *, context: XaiContext) -> Sequence[ProvenanceLink]:
        return await self._records.parents(entity_id, context=context)

    async def children(self, entity_id: str, *, context: XaiContext) -> Sequence[ProvenanceLink]:
        return await self._records.children(entity_id, context=context)

    async def delete_run(self, context: XaiContext) -> int:
        return await self._records.delete_run(context)


class TestAuditedStoreHonoursTheAgxaiContract(ProvenanceStoreContract):
    async def make_store(self) -> ProvenanceStore:
        return AuditedStore()


class State(TypedDict, total=False):
    step: str


def _graph() -> Any:
    builder = StateGraph(State)
    builder.add_node("work", lambda state: {"step": "done"})
    builder.add_edge(START, "work")
    builder.add_edge("work", END)
    return builder.compile()


def _runtime() -> XAIRuntime:
    return XAIRuntime(application_id="app", tenant_id="tenant", graph_id="graph")


async def _feedback_for(manager: FeedbackManager, run_id: str) -> Any:
    return await manager.submit(
        source="evaluator",
        category="quality",
        target=FeedbackTarget(type="run", id=run_id),
        execution_context=ExecutionContext(run_id=run_id, node_id="work"),
    )


async def test_a_store_registered_on_the_runtime_is_used_by_default() -> None:
    xai = _runtime()
    store = AuditedStore()
    xai.register(ProvenanceStore, store)
    provider = AgxaiProvenanceProvider(xai)
    manager = FeedbackManager(provenance_provider=provider, learning=False)
    with xai.collect_runs() as runs:
        await xai.instrument(_graph()).ainvoke({})

    feedback = await _feedback_for(manager, str(runs[0].run_id))

    assert provider.store is store
    assert feedback.provenance is not None
    assert feedback.provenance.execution_id == str(runs[0].execution.id)
    assert store.queries == 1


async def test_an_explicit_store_takes_precedence_over_the_runtime_store() -> None:
    xai = _runtime()
    with xai.collect_runs() as runs:
        await xai.instrument(_graph()).ainvoke({})
    replica = AuditedStore()
    await replica.write(runs[0].execution)
    xai.registry.remove(ProvenanceStore)
    provider = AgxaiProvenanceProvider(xai, store=replica)
    manager = FeedbackManager(provenance_provider=provider, learning=False)

    feedback = await _feedback_for(manager, str(runs[0].run_id))

    assert provider.store is replica
    assert feedback.provenance is not None
    assert feedback.provenance.node_execution_id is not None
    assert replica.queries == 1


def test_the_store_must_be_an_agxai_provenance_store() -> None:
    with pytest.raises(FeedbackConfigurationError, match="ProvenanceStore"):
        AgxaiProvenanceProvider(_runtime(), store=object())  # type: ignore[arg-type]
