from unittest.mock import patch
from uuid import uuid4

import pytest

from feedback_manager import ExecutionContext, FeedbackManager, FeedbackQuery
from feedback_manager.core.targets import FeedbackTarget, FeedbackTargetType
from feedback_manager.errors import (
    FeedbackNotFoundError,
    FeedbackStoreError,
    FeedbackValidationError,
)

TARGET = FeedbackTarget(type=FeedbackTargetType.RUN, id="run")


async def test_tenant_idempotency_and_query():
    manager = FeedbackManager(learning=False)
    alice, bob = manager.for_tenant("alice"), manager.for_tenant("bob")
    first = await alice.submit(
        source="human", category="rating", target=TARGET, idempotency_key="same"
    )
    second = await bob.submit(
        source="human", category="rating", target=TARGET, idempotency_key="same"
    )
    unscoped = await manager.submit(
        source="human", category="rating", target=TARGET, idempotency_key="same"
    )
    assert len({first.feedback_id, second.feedback_id, unscoped.feedback_id}) == 3
    assert (
        await alice.submit(source="human", category="rating", target=TARGET, idempotency_key="same")
        == first
    )
    assert (
        await manager.submit(
            source="human", category="rating", target=TARGET, idempotency_key="same"
        )
        == unscoped
    )
    assert await alice.query() == [first]
    assert await alice.query(FeedbackQuery(idempotency_key="same")) == [first]
    assert await bob.get(first.feedback_id) is None
    assert await alice.get(uuid4()) is None
    assert await alice.get(first.feedback_id) == first
    assert len(await manager.query(FeedbackQuery(idempotency_key="same"))) == 3
    assert await manager.query(FeedbackQuery(unscoped_only=True)) == [unscoped]
    assert await alice.query(FeedbackQuery(tenant_id="bob")) == [first]


async def test_tenant_lifecycle_never_mutates_foreign_feedback():
    manager = FeedbackManager(learning=False)
    owner, stranger = manager.for_tenant("owner"), manager.for_tenant("stranger")
    event = await owner.submit(source="human", category="rating", target=TARGET)
    for action in (
        stranger.acknowledge,
        stranger.mark_handled,
        stranger.resolve,
        stranger.reject,
        stranger.cancel,
        stranger.expire,
    ):
        with pytest.raises(FeedbackNotFoundError):
            await action(event.feedback_id)
    await owner.acknowledge(event.feedback_id)
    await owner.mark_handled(event.feedback_id)
    resolved = await owner.resolve(event.feedback_id, resolution={"answer": "done"})
    assert resolved.resolution == {"answer": "done"}
    for action in (owner.reject, owner.cancel, owner.expire):
        fresh = await owner.submit(source="human", category="rating", target=TARGET)
        assert (await action(fresh.feedback_id)).feedback_id == fresh.feedback_id


async def test_invalid_scope_and_untrusted_store():
    manager = FeedbackManager(learning=False)
    with pytest.raises(FeedbackValidationError, match="blank"):
        manager.for_tenant(" ")
    with pytest.raises(FeedbackValidationError, match="non-empty"):
        FeedbackQuery(tenant_id=" ")
    with pytest.raises(FeedbackValidationError, match="unscoped_only"):
        FeedbackQuery(tenant_id="tenant", unscoped_only=True)
    scoped = manager.for_tenant("tenant")
    with pytest.raises(FeedbackValidationError, match="another tenant"):
        await scoped.submit(
            source="human",
            category="rating",
            target=TARGET,
            execution_context=ExecutionContext(tenant_id="other"),
        )
    event = await scoped.submit(
        source="human",
        category="rating",
        target=TARGET,
        execution_context=ExecutionContext(run_id="run"),
    )
    assert event.execution_context is not None
    assert event.execution_context.tenant_id == "tenant"
    foreign = await manager.for_tenant("other").submit(
        source="human", category="rating", target=TARGET
    )
    with (
        patch.object(manager, "query", return_value=[foreign]),
        pytest.raises(FeedbackStoreError, match="another tenant"),
    ):
        await scoped.query()
    with (
        patch.object(manager._store, "query", return_value=[foreign]),
        pytest.raises(FeedbackStoreError, match="another tenant"),
    ):
        await scoped.submit(
            source="human", category="rating", target=TARGET, idempotency_key="replay"
        )


async def test_scoped_delivery():
    from unittest.mock import AsyncMock

    manager = FeedbackManager(learning=False)
    tenant = manager.for_tenant("tenant")
    receive = AsyncMock()
    subscription = tenant.subscribe(receive)
    stream = tenant.stream()
    await manager.for_tenant("foreign").submit(source="human", category="rating", target=TARGET)
    mine = await tenant.submit(source="human", category="rating", target=TARGET)
    assert await anext(stream) == mine
    receive.assert_awaited_once_with(mine)
    subscription.cancel()
    await stream.aclose()
    await manager.aclose()
