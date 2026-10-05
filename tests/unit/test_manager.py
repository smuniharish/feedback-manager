"""`FeedbackManager`: construction, submission, lifecycle, retrieval, and isolation."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from feedback_manager import (
    ExecutionContext,
    FeedbackConflictError,
    FeedbackEvent,
    FeedbackLifecycleError,
    FeedbackManager,
    FeedbackManagerError,
    FeedbackNotFoundError,
    FeedbackQuery,
    FeedbackStatus,
    FeedbackStoreError,
    FeedbackTarget,
    FeedbackValidationError,
)
from feedback_manager.contracts import (
    FeedbackHandler,
    FeedbackHandlerResult,
    FeedbackLifecyclePolicy,
    FeedbackRedactionPolicy,
    FeedbackRouter,
)
from feedback_manager.errors import (
    FeedbackConfigurationError,
    FeedbackCorrelationError,
    FeedbackHandlerError,
    FeedbackRoutingError,
)
from feedback_manager.policies import FailureMode, FailurePolicy, FeedbackStage, RetentionPolicy
from feedback_manager.routing import DefaultFeedbackRouter
from feedback_manager.storage import InMemoryFeedbackStore

if TYPE_CHECKING:
    from collections.abc import Sequence

    from feedback_manager.core import JsonObject
    from tests.conftest import RecordingSink


class Recorder(FeedbackHandler):
    def __init__(self, *, handled: bool = True) -> None:
        self.seen: list[FeedbackEvent] = []
        self._handled = handled

    async def handle(self, feedback: FeedbackEvent) -> FeedbackHandlerResult:
        self.seen.append(feedback)
        return FeedbackHandlerResult(handled=self._handled)


class Exploding(FeedbackHandler):
    async def handle(self, feedback: FeedbackEvent) -> FeedbackHandlerResult:
        raise RuntimeError("handler exploded")


async def _submit(
    manager: FeedbackManager, target: FeedbackTarget, **fields: object
) -> FeedbackEvent:
    return await manager.submit(source="human", category="correction", target=target, **fields)  # type: ignore[arg-type]


class TestConstruction:
    @pytest.mark.parametrize(
        "name",
        [
            "store",
            "router",
            "correlator",
            "lifecycle_policy",
            "redaction_policy",
            "failure_policy",
            "observability_sink",
            "xai_runtime",
        ],
    )
    def test_wrongly_typed_collaborators_are_rejected(self, name: str) -> None:
        with pytest.raises(FeedbackConfigurationError, match=name):
            FeedbackManager(**{name: object()})  # type: ignore[arg-type]

    async def test_instances_are_independent(self, target: FeedbackTarget) -> None:
        first, second = FeedbackManager(), FeedbackManager()

        event = await _submit(first, target)

        assert await second.get(event.feedback_id) is None
        assert len(await first.query()) == 1


class TestSubmit:
    async def test_stores_received_feedback_with_defaults(
        self, manager: FeedbackManager, sink: RecordingSink, target: FeedbackTarget
    ) -> None:
        event = await _submit(manager, target, metadata={"channel": "ui"})

        assert event.status is FeedbackStatus.RECEIVED
        assert event.payload == {}
        assert event.metadata == {"channel": "ui"}
        assert event.correlation_id == str(event.feedback_id)
        assert event.provenance is None
        assert await manager.get(event.feedback_id) == event
        assert sink.names == ["feedback.received"]
        assert sink.events[0].attributes == {
            "source": "human",
            "category": "correction",
            "target_type": "generation",
            "status": "received",
        }

    @pytest.mark.parametrize(
        "fields",
        [
            {"source": ""},
            {"category": " padded"},
            {"payload": {"value": object()}},
            {"idempotency_key": ""},
            {"execution_context": {"run_id": 7}},
        ],
    )
    async def test_invalid_input_raises_validation_errors(
        self, manager: FeedbackManager, target: FeedbackTarget, fields: dict[str, object]
    ) -> None:
        arguments: dict[str, object] = {"source": "human", "category": "rating", "target": target}
        arguments.update(fields)

        with pytest.raises(FeedbackValidationError, match="invalid feedback") as raised:
            await manager.submit(**arguments)  # type: ignore[arg-type]

        assert isinstance(raised.value.__cause__, ValidationError)
        assert await manager.query() == []

    async def test_idempotent_retries_return_the_original_without_reprocessing(
        self, sink: RecordingSink, target: FeedbackTarget
    ) -> None:
        handler = Recorder()
        manager = FeedbackManager(
            router=DefaultFeedbackRouter(default_handlers=(handler,)), observability_sink=sink
        )
        first = await _submit(manager, target, idempotency_key="retry-1")
        await manager.acknowledge(first.feedback_id)

        again = await _submit(manager, target, idempotency_key="retry-1")

        assert again.feedback_id == first.feedback_id
        assert again.status is FeedbackStatus.ACKNOWLEDGED
        assert len(handler.seen) == 1
        assert sink.names.count("feedback.received") == 1

    async def test_replays_skip_redaction_correlation_and_provenance(
        self, sink: RecordingSink, target: FeedbackTarget
    ) -> None:
        class FailsAfterFirst:
            def __init__(self) -> None:
                self.calls = 0

            async def correlate(self, feedback: FeedbackEvent) -> str:
                self.calls += 1
                if self.calls > 1:
                    raise ConnectionError("lookup service down")
                return "conversation-1"

        correlator = FailsAfterFirst()
        manager = FeedbackManager(
            correlator=correlator,
            failure_policy=FailurePolicy(modes={FeedbackStage.CORRELATION: FailureMode.BLOCKING}),
            observability_sink=sink,
        )
        first = await _submit(manager, target, idempotency_key="retry-1")

        again = await _submit(manager, target, idempotency_key="retry-1")

        assert again == first
        assert correlator.calls == 1
        assert sink.names == ["feedback.received"]

    async def test_redaction_runs_before_storage(self, target: FeedbackTarget) -> None:
        class MaskEmail(FeedbackRedactionPolicy):
            def redact(self, feedback: FeedbackEvent) -> FeedbackEvent:
                return feedback.model_copy(update={"payload": {"email": "***"}})

        manager = FeedbackManager(redaction_policy=MaskEmail())

        event = await _submit(manager, target, payload={"email": "a@example.com"})

        assert event.payload == {"email": "***"}

    async def test_redaction_failures_never_store_unredacted_feedback(
        self, target: FeedbackTarget
    ) -> None:
        class Broken(FeedbackRedactionPolicy):
            def __init__(self, error: Exception | None) -> None:
                self.error = error

            def redact(self, feedback: FeedbackEvent) -> FeedbackEvent:
                if self.error is not None:
                    raise self.error
                return "not an event"  # type: ignore[return-value]

        bug = FeedbackManager(redaction_policy=Broken(KeyError("email")))
        with pytest.raises(FeedbackValidationError, match="redaction policy failed") as raised:
            await _submit(bug, target)
        assert isinstance(raised.value.__cause__, KeyError)

        refusal = FeedbackManager(redaction_policy=Broken(FeedbackValidationError("refused")))
        with pytest.raises(FeedbackValidationError, match="refused"):
            await _submit(refusal, target)

        wrong = FeedbackManager(redaction_policy=Broken(None))
        with pytest.raises(FeedbackConfigurationError, match="must return a FeedbackEvent"):
            await _submit(wrong, target)

        for manager in (bug, refusal, wrong):
            assert await manager.query() == []

    async def test_custom_correlators_group_feedback(self, target: FeedbackTarget) -> None:
        class ByConversation:
            async def correlate(self, feedback: FeedbackEvent) -> str:
                return str(feedback.metadata["conversation"])

        manager = FeedbackManager(correlator=ByConversation())
        await _submit(manager, target, metadata={"conversation": "c-1"})
        await _submit(manager, target, metadata={"conversation": "c-2"})
        await _submit(manager, target, metadata={"conversation": "c-1"})

        assert len(await manager.query(FeedbackQuery(correlation_id="c-1"))) == 2

    @pytest.mark.parametrize("value", ["", " padded", 42])
    async def test_invalid_correlation_ids_fall_back_to_the_default(
        self, sink: RecordingSink, target: FeedbackTarget, value: object
    ) -> None:
        class Invalid:
            async def correlate(self, feedback: FeedbackEvent) -> object:
                return value

        manager = FeedbackManager(correlator=Invalid(), observability_sink=sink)  # type: ignore[arg-type]

        event = await _submit(manager, target, execution_context=ExecutionContext(run_id="r-1"))

        assert event.correlation_id == "r-1"
        failed = sink.events[0]
        assert failed.name == "feedback.failed"
        assert failed.attributes["stage"] == "correlation"
        assert failed.attributes["error_type"] == "FeedbackValidationError"
        assert failed.attributes["status"] == "created"

    async def test_blocking_correlation_failures_stop_submission(
        self, target: FeedbackTarget
    ) -> None:
        class Broken:
            async def correlate(self, feedback: FeedbackEvent) -> str:
                raise ConnectionError("lookup service down")

        manager = FeedbackManager(
            correlator=Broken(),
            failure_policy=FailurePolicy(modes={FeedbackStage.CORRELATION: FailureMode.BLOCKING}),
        )

        with pytest.raises(FeedbackCorrelationError) as raised:
            await _submit(manager, target)

        assert isinstance(raised.value.__cause__, ConnectionError)
        assert await manager.query() == []


class TestRouting:
    async def test_handlers_run_in_order_and_are_counted(
        self, sink: RecordingSink, target: FeedbackTarget
    ) -> None:
        first, skipped = Recorder(), Recorder(handled=False)
        manager = FeedbackManager(
            router=DefaultFeedbackRouter(default_handlers=(first, Exploding(), skipped)),
            observability_sink=sink,
        )

        event = await _submit(manager, target)

        assert first.seen == [event] == skipped.seen
        routed = sink.events[-1]
        assert routed.name == "feedback.routed"
        assert routed.attributes["handler_count"] == 3
        assert routed.attributes["handled_count"] == 1
        assert sink.names.count("feedback.failed") == 1

    async def test_blocking_handler_failures_propagate_after_storage(
        self, target: FeedbackTarget
    ) -> None:
        manager = FeedbackManager(
            router=DefaultFeedbackRouter(default_handlers=(Exploding(),)),
            failure_policy=FailurePolicy(modes={FeedbackStage.HANDLER: FailureMode.BLOCKING}),
        )

        with pytest.raises(FeedbackHandlerError, match="handler exploded") as raised:
            await _submit(manager, target)

        assert raised.value.feedback_id is not None
        stored = await manager.get(raised.value.feedback_id)
        assert stored is not None
        assert stored.status is FeedbackStatus.RECEIVED

    async def test_router_failures_skip_routing(
        self, sink: RecordingSink, target: FeedbackTarget
    ) -> None:
        class BrokenRouter(FeedbackRouter):
            async def route(self, feedback: FeedbackEvent) -> Sequence[FeedbackHandler]:
                raise LookupError("no rules")

        manager = FeedbackManager(router=BrokenRouter(), observability_sink=sink)

        event = await _submit(manager, target)

        assert event.status is FeedbackStatus.RECEIVED
        assert sink.names == ["feedback.received", "feedback.failed"]

    @pytest.mark.parametrize("selection", [None, [object()], ["not a handler"]])
    async def test_routers_must_select_handlers(
        self, sink: RecordingSink, target: FeedbackTarget, selection: object
    ) -> None:
        class WrongRouter(FeedbackRouter):
            async def route(self, feedback: FeedbackEvent) -> Sequence[FeedbackHandler]:
                return selection  # type: ignore[return-value]

        isolated = FeedbackManager(router=WrongRouter(), observability_sink=sink)
        event = await _submit(isolated, target)
        assert event.status is FeedbackStatus.RECEIVED
        failed = sink.events[-1]
        assert (failed.name, failed.attributes["stage"]) == ("feedback.failed", "routing")
        assert failed.attributes["error_type"] == "TypeError"

        blocking = FeedbackManager(
            router=WrongRouter(),
            failure_policy=FailurePolicy(modes={FeedbackStage.ROUTING: FailureMode.BLOCKING}),
        )
        with pytest.raises(FeedbackRoutingError) as raised:
            await _submit(blocking, target)
        assert isinstance(raised.value.__cause__, TypeError)

    async def test_handlers_must_return_a_result(
        self, sink: RecordingSink, target: FeedbackTarget
    ) -> None:
        class ReturnsBool(FeedbackHandler):
            async def handle(self, feedback: FeedbackEvent) -> FeedbackHandlerResult:
                return True  # type: ignore[return-value]

        after = Recorder()
        isolated = FeedbackManager(
            router=DefaultFeedbackRouter(default_handlers=(ReturnsBool(), after)),
            observability_sink=sink,
        )
        event = await _submit(isolated, target)
        assert after.seen == [event]
        failed, routed = sink.events[-2:]
        assert (failed.attributes["stage"], failed.attributes["error_type"]) == (
            "handler",
            "TypeError",
        )
        assert (routed.attributes["handler_count"], routed.attributes["handled_count"]) == (2, 1)

        blocking = FeedbackManager(
            router=DefaultFeedbackRouter(default_handlers=(ReturnsBool(),)),
            failure_policy=FailurePolicy(modes={FeedbackStage.HANDLER: FailureMode.BLOCKING}),
        )
        with pytest.raises(FeedbackHandlerError, match="not a FeedbackHandlerResult") as raised:
            await _submit(blocking, target)
        assert isinstance(raised.value.__cause__, TypeError)

    async def test_without_handlers_nothing_is_routed(
        self, manager: FeedbackManager, sink: RecordingSink, target: FeedbackTarget
    ) -> None:
        await _submit(manager, target)

        assert "feedback.routed" not in sink.names


class TestObservabilityIsolation:
    async def test_a_failing_sink_never_breaks_processing(
        self, target: FeedbackTarget, caplog: pytest.LogCaptureFixture
    ) -> None:
        class BrokenSink:
            def emit(self, event: object) -> None:
                raise ConnectionError("collector unreachable")

        manager = FeedbackManager(observability_sink=BrokenSink())

        with caplog.at_level(logging.WARNING, logger="feedback_manager.manager"):
            event = await _submit(manager, target)
            acknowledged = await manager.acknowledge(event.feedback_id)

        assert acknowledged.status is FeedbackStatus.ACKNOWLEDGED
        assert caplog.text.count("observability sink failed") == 2
        assert caplog.records[0].exc_info is not None


class FailingStore(InMemoryFeedbackStore):
    def __init__(self, error: Exception, *, operation: str) -> None:
        super().__init__()
        self.error = error
        self.operation = operation

    async def create(self, feedback: FeedbackEvent) -> FeedbackEvent:
        if self.operation == "create":
            raise self.error
        return await super().create(feedback)

    async def get(self, feedback_id: UUID) -> FeedbackEvent | None:
        if self.operation == "get":
            raise self.error
        return await super().get(feedback_id)

    async def query(self, query: FeedbackQuery) -> Sequence[FeedbackEvent]:
        if self.operation == "query":
            raise self.error
        return await super().query(query)


class TestStoreFailures:
    async def test_unexpected_store_errors_become_store_errors(
        self, sink: RecordingSink, target: FeedbackTarget
    ) -> None:
        manager = FeedbackManager(
            store=FailingStore(ConnectionError("db down"), operation="create"),
            observability_sink=sink,
        )

        with pytest.raises(FeedbackStoreError, match="db down") as raised:
            await _submit(manager, target)

        assert isinstance(raised.value.__cause__, ConnectionError)
        assert raised.value.feedback_id is not None
        assert sink.names == ["feedback.failed"]
        assert sink.events[0].attributes["stage"] == "store"

    async def test_a_failing_replay_lookup_is_reported(
        self, sink: RecordingSink, target: FeedbackTarget
    ) -> None:
        manager = FeedbackManager(
            store=FailingStore(ConnectionError("db down"), operation="query"),
            observability_sink=sink,
        )

        with pytest.raises(FeedbackStoreError, match="db down"):
            await _submit(manager, target, idempotency_key="retry-1")

        assert sink.names == ["feedback.failed"]
        assert sink.events[0].attributes["stage"] == "store"

    async def test_typed_store_errors_propagate_unchanged(
        self, sink: RecordingSink, target: FeedbackTarget
    ) -> None:
        error = FeedbackStoreError("quota exceeded")
        manager = FeedbackManager(
            store=FailingStore(error, operation="create"), observability_sink=sink
        )

        with pytest.raises(FeedbackStoreError) as raised:
            await _submit(manager, target)

        assert raised.value is error
        assert sink.names == ["feedback.failed"]

    @pytest.mark.parametrize("error", [ConnectionError("db down"), FeedbackStoreError("down")])
    async def test_read_failures_raise_store_errors(
        self, sink: RecordingSink, error: Exception
    ) -> None:
        manager = FeedbackManager(
            store=FailingStore(error, operation="get"), observability_sink=sink
        )

        with pytest.raises(FeedbackStoreError):
            await manager.get(uuid4())
        with pytest.raises(FeedbackStoreError):
            await manager.acknowledge(uuid4())

        assert sink.names == []


class TestLifecycle:
    async def test_happy_path(
        self, manager: FeedbackManager, sink: RecordingSink, target: FeedbackTarget
    ) -> None:
        event = await _submit(manager, target)

        await manager.acknowledge(event.feedback_id)
        await manager.mark_handled(event.feedback_id)
        resolved = await manager.resolve(event.feedback_id, resolution={"applied": True})

        assert resolved.status is FeedbackStatus.RESOLVED
        assert resolved.resolution == {"applied": True}
        assert sink.names == [
            "feedback.received",
            "feedback.acknowledged",
            "feedback.handled",
            "feedback.resolved",
        ]

    @pytest.mark.parametrize(
        ("close", "status", "name"),
        [
            ("reject", FeedbackStatus.REJECTED, "feedback.rejected"),
            ("cancel", FeedbackStatus.CANCELLED, "feedback.cancelled"),
            ("expire", FeedbackStatus.EXPIRED, "feedback.expired"),
        ],
    )
    async def test_terminal_moves_emit_their_own_event(
        self,
        manager: FeedbackManager,
        sink: RecordingSink,
        target: FeedbackTarget,
        close: str,
        status: FeedbackStatus,
        name: str,
    ) -> None:
        event = await _submit(manager, target)

        closed = await getattr(manager, close)(event.feedback_id)

        assert closed.status is status
        assert closed.resolution is None
        assert sink.names[-1] == name

    @pytest.mark.parametrize(
        ("arguments", "expected"),
        [
            ({"reason": "duplicate"}, {"reason": "duplicate"}),
            ({"resolution": {"ticket": "T-1"}}, {"ticket": "T-1"}),
            (
                {"reason": "duplicate", "resolution": {"ticket": "T-1"}},
                {"ticket": "T-1", "reason": "duplicate"},
            ),
            ({"reason": ""}, None),
        ],
    )
    async def test_reject_and_cancel_resolutions(
        self,
        manager: FeedbackManager,
        target: FeedbackTarget,
        arguments: dict[str, object],
        expected: JsonObject | None,
    ) -> None:
        rejected = await manager.reject((await _submit(manager, target)).feedback_id, **arguments)  # type: ignore[arg-type]
        cancelled = await manager.cancel((await _submit(manager, target)).feedback_id, **arguments)  # type: ignore[arg-type]

        assert rejected.resolution == expected == cancelled.resolution

    async def test_illegal_moves_change_nothing(
        self, manager: FeedbackManager, sink: RecordingSink, target: FeedbackTarget
    ) -> None:
        event = await _submit(manager, target)

        with pytest.raises(FeedbackLifecycleError):
            await manager.resolve(event.feedback_id, resolution={"applied": True})

        assert await manager.get(event.feedback_id) == event
        assert sink.names == ["feedback.received"]

    async def test_repeated_moves_are_silent_no_ops(
        self, manager: FeedbackManager, sink: RecordingSink, target: FeedbackTarget
    ) -> None:
        notified: list[FeedbackStatus] = []

        async def subscriber(feedback: FeedbackEvent) -> None:
            notified.append(feedback.status)

        manager.subscribe(subscriber)
        event = await _submit(manager, target)
        first = await manager.reject(event.feedback_id, reason="spam")

        again = await manager.reject(event.feedback_id, reason="something else")

        assert again == first
        assert again.resolution == {"reason": "spam"}
        assert notified == [FeedbackStatus.RECEIVED, FeedbackStatus.REJECTED]
        assert sink.names == ["feedback.received", "feedback.rejected"]

    async def test_terminal_feedback_cannot_be_reopened(
        self, manager: FeedbackManager, target: FeedbackTarget
    ) -> None:
        event = await _submit(manager, target)
        await manager.cancel(event.feedback_id)

        with pytest.raises(FeedbackLifecycleError, match="terminal"):
            await manager.acknowledge(event.feedback_id)

    async def test_unknown_feedback(self, manager: FeedbackManager) -> None:
        with pytest.raises(FeedbackNotFoundError):
            await manager.acknowledge(uuid4())

    async def test_resolutions_must_be_json(
        self, manager: FeedbackManager, target: FeedbackTarget
    ) -> None:
        event = await _submit(manager, target)

        with pytest.raises(FeedbackValidationError, match="invalid resolution"):
            await manager.reject(event.feedback_id, resolution={"at": object()})

    async def test_lifecycle_policies_authorize_every_move(self, target: FeedbackTarget) -> None:
        class ReviewerOnly(FeedbackLifecyclePolicy):
            def authorize_transition(self, feedback: FeedbackEvent, target: FeedbackStatus) -> None:
                if target is FeedbackStatus.RESOLVED and "reviewer" not in feedback.metadata:
                    raise FeedbackLifecycleError("a reviewer must resolve feedback")
                if target is FeedbackStatus.CANCELLED:
                    raise KeyError("policy bug")

        manager = FeedbackManager(lifecycle_policy=ReviewerOnly())
        event = await _submit(manager, target)
        await manager.acknowledge(event.feedback_id)
        await manager.mark_handled(event.feedback_id)

        with pytest.raises(FeedbackLifecycleError, match="reviewer"):
            await manager.resolve(event.feedback_id)
        with pytest.raises(FeedbackLifecycleError, match="lifecycle policy failed") as raised:
            await manager.cancel(event.feedback_id)
        assert isinstance(raised.value.__cause__, KeyError)
        stored = await manager.get(event.feedback_id)
        assert stored is not None
        assert stored.status is FeedbackStatus.HANDLED

    async def test_concurrent_changes_are_retried_against_fresh_state(
        self, sink: RecordingSink, target: FeedbackTarget
    ) -> None:
        class RacingStore(InMemoryFeedbackStore):
            raced = False

            async def transition(
                self,
                feedback_id: UUID,
                status: FeedbackStatus,
                *,
                expected: FeedbackStatus,
                resolution: JsonObject | None = None,
            ) -> FeedbackEvent:
                if not self.raced:
                    self.raced = True
                    await super().transition(
                        feedback_id, FeedbackStatus.ACKNOWLEDGED, expected=expected
                    )
                return await super().transition(
                    feedback_id, status, expected=expected, resolution=resolution
                )

        class Spy(FeedbackLifecyclePolicy):
            def __init__(self) -> None:
                self.seen: list[FeedbackStatus] = []

            def authorize_transition(self, feedback: FeedbackEvent, target: FeedbackStatus) -> None:
                self.seen.append(feedback.status)

        policy = Spy()
        manager = FeedbackManager(
            store=RacingStore(), lifecycle_policy=policy, observability_sink=sink
        )
        event = await _submit(manager, target)

        cancelled = await manager.cancel(event.feedback_id, reason="withdrawn")

        assert cancelled.status is FeedbackStatus.CANCELLED
        assert policy.seen == [FeedbackStatus.RECEIVED, FeedbackStatus.ACKNOWLEDGED]
        assert sink.names == ["feedback.received", "feedback.cancelled"]

    async def test_endless_conflicts_give_up(self, target: FeedbackTarget) -> None:
        class AlwaysConflicting(InMemoryFeedbackStore):
            async def transition(
                self,
                feedback_id: UUID,
                status: FeedbackStatus,
                *,
                expected: FeedbackStatus,
                resolution: JsonObject | None = None,
            ) -> FeedbackEvent:
                raise FeedbackConflictError("changed", feedback_id=feedback_id)

        manager = FeedbackManager(store=AlwaysConflicting())
        event = await _submit(manager, target)

        with pytest.raises(FeedbackConflictError, match="kept changing"):
            await manager.acknowledge(event.feedback_id)


class TestRetention:
    async def test_expire_overdue_expires_only_expirable_pending_feedback(
        self, target: FeedbackTarget
    ) -> None:
        class KeepFlagged(FeedbackLifecyclePolicy):
            def authorize_transition(self, feedback: FeedbackEvent, target: FeedbackStatus) -> None:
                if feedback.metadata.get("keep"):
                    raise FeedbackLifecycleError("kept by policy")

        store = InMemoryFeedbackStore()
        manager = FeedbackManager(store=store, lifecycle_policy=KeepFlagged())
        received = await _submit(manager, target)
        acknowledged = await _submit(manager, target)
        await manager.acknowledge(acknowledged.feedback_id)
        handled = await _submit(manager, target)
        await manager.acknowledge(handled.feedback_id)
        await manager.mark_handled(handled.feedback_id)
        kept = await _submit(manager, target, metadata={"keep": True})
        created = await store.create(
            FeedbackEvent(source="system", category="comment", target=target)
        )
        policy = RetentionPolicy(max_pending_age=timedelta(hours=1))

        expired = await manager.expire_overdue(policy, now=datetime.now(UTC) + timedelta(hours=2))

        assert {event.feedback_id for event in expired} == {
            created.feedback_id,
            received.feedback_id,
            acknowledged.feedback_id,
        }
        assert all(event.status is FeedbackStatus.EXPIRED for event in expired)
        for pending in (handled, kept):
            stored = await manager.get(pending.feedback_id)
            assert stored is not None
            assert stored.status is not FeedbackStatus.EXPIRED

    async def test_nothing_is_overdue_yet(
        self, manager: FeedbackManager, target: FeedbackTarget
    ) -> None:
        await _submit(manager, target)

        assert (
            await manager.expire_overdue(RetentionPolicy(max_pending_age=timedelta(days=1))) == []
        )

    async def test_feedback_expired_or_deleted_meanwhile_is_skipped(
        self, target: FeedbackTarget
    ) -> None:
        class RacingStore(InMemoryFeedbackStore):
            """Answers each overdue query with a stale snapshot, as another worker races ahead."""

            def __init__(self) -> None:
                super().__init__()
                self.deleted = FeedbackEvent(
                    source="human",
                    category="comment",
                    target=target,
                    status=FeedbackStatus.RECEIVED,
                )

            async def query(self, query: FeedbackQuery) -> Sequence[FeedbackEvent]:
                snapshot = list(await super().query(query))
                for event in snapshot:
                    await self.transition(
                        event.feedback_id, FeedbackStatus.EXPIRED, expected=event.status
                    )
                if query.status is FeedbackStatus.RECEIVED:
                    snapshot.append(self.deleted)
                return snapshot

        manager = FeedbackManager(store=RacingStore())
        raced = await _submit(manager, target)

        expired = await manager.expire_overdue(
            RetentionPolicy(max_pending_age=timedelta(hours=1)),
            now=datetime.now(UTC) + timedelta(hours=2),
        )

        assert expired == []
        stored = await manager.get(raced.feedback_id)
        assert stored is not None
        assert stored.status is FeedbackStatus.EXPIRED


class TestRetrieval:
    async def test_query_defaults_to_everything(
        self, manager: FeedbackManager, target: FeedbackTarget
    ) -> None:
        human = await _submit(manager, target)
        tool = await manager.submit(source="tool", category="timeout", target=target)

        assert [event.feedback_id for event in await manager.query()] == [
            human.feedback_id,
            tool.feedback_id,
        ]
        assert await manager.query(FeedbackQuery(source="tool")) == [tool]
        assert await manager.get(uuid4()) is None

    def test_every_error_is_a_feedback_manager_error(self) -> None:
        assert issubclass(FeedbackStoreError, FeedbackManagerError)
