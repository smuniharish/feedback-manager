"""`FeedbackManager`: the application-facing service."""

from __future__ import annotations

import asyncio
import threading
from functools import cache, partial
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Self

from pydantic import ConfigDict, TypeAdapter, ValidationError

from feedback_manager import _logging
from feedback_manager.api.scoped import TenantFeedbackManager
from feedback_manager.api.stream import FeedbackStream
from feedback_manager.api.subscription import Subscription
from feedback_manager.contracts.changes import FeedbackChangeSource
from feedback_manager.contracts.correlator import FeedbackCorrelator
from feedback_manager.contracts.handler import FeedbackHandler, FeedbackHandlerResult
from feedback_manager.contracts.policy import FeedbackLifecyclePolicy, FeedbackRedactionPolicy
from feedback_manager.contracts.provenance import ProvenanceProvider
from feedback_manager.contracts.router import FeedbackRouter
from feedback_manager.contracts.store import FeedbackQuery, FeedbackStore
from feedback_manager.contracts.subscriber import FeedbackSubscriber, FunctionSubscriber
from feedback_manager.core._types import JsonObject
from feedback_manager.core.events import FeedbackEvent
from feedback_manager.core.lifecycle import validate_transition
from feedback_manager.core.status import FeedbackStatus
from feedback_manager.correlation.correlator import (
    DefaultFeedbackCorrelator,
    default_correlation_id,
)
from feedback_manager.errors import (
    FeedbackConfigurationError,
    FeedbackConflictError,
    FeedbackLifecycleError,
    FeedbackManagerError,
    FeedbackNotFoundError,
    FeedbackStoreError,
    FeedbackValidationError,
)
from feedback_manager.learning.engine import LearningEngine
from feedback_manager.learning.jobs import InMemoryLearningJobStore, LearningJobStore
from feedback_manager.learning.worker import (
    BackgroundLearner,
    ContextScopeResolver,
    ScopeResolver,
    as_scope_resolver,
)
from feedback_manager.observability.hooks import (
    FEEDBACK_ACKNOWLEDGED,
    FEEDBACK_CANCELLED,
    FEEDBACK_EXPIRED,
    FEEDBACK_FAILED,
    FEEDBACK_HANDLED,
    FEEDBACK_RECEIVED,
    FEEDBACK_REJECTED,
    FEEDBACK_RESOLVED,
    FEEDBACK_ROUTED,
    LoggingObservabilitySink,
    ObservabilityEvent,
    ObservabilitySink,
)
from feedback_manager.policies.failure import FailurePolicy, FeedbackStage
from feedback_manager.routing.default_router import DefaultFeedbackRouter
from feedback_manager.storage.artifacts.memory import InMemoryArtifactRepository
from feedback_manager.storage.memory import InMemoryFeedbackStore

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence
    from datetime import datetime
    from types import TracebackType
    from uuid import UUID

    from feedback_manager.core.categories import FeedbackCategory
    from feedback_manager.core.context import ExecutionContext
    from feedback_manager.core.sources import FeedbackSource
    from feedback_manager.core.targets import FeedbackTarget
    from feedback_manager.learning.models import ArtifactScope
    from feedback_manager.policies.retention import RetentionPolicy

_logger = _logging.get_logger("feedback_manager.manager")

_STATUS_EVENTS: Mapping[FeedbackStatus, str] = MappingProxyType(
    {
        FeedbackStatus.ACKNOWLEDGED: FEEDBACK_ACKNOWLEDGED,
        FeedbackStatus.HANDLED: FEEDBACK_HANDLED,
        FeedbackStatus.RESOLVED: FEEDBACK_RESOLVED,
        FeedbackStatus.REJECTED: FEEDBACK_REJECTED,
        FeedbackStatus.CANCELLED: FEEDBACK_CANCELLED,
        FeedbackStatus.EXPIRED: FEEDBACK_EXPIRED,
    }
)
_EXPIRABLE = (FeedbackStatus.CREATED, FeedbackStatus.RECEIVED, FeedbackStatus.ACKNOWLEDGED)
# Every conflict means the event advanced, and the lifecycle has no cycles, so
# this bound is never reached by a store that reports conflicts truthfully.
_TRANSITION_ATTEMPTS = len(FeedbackStatus)
_CALLER_ERRORS = (FeedbackValidationError, FeedbackNotFoundError, FeedbackLifecycleError)
_EVERYTHING = FeedbackQuery()


@cache
def _resolution_adapter() -> TypeAdapter[JsonObject]:
    return TypeAdapter(JsonObject, config=ConfigDict(allow_inf_nan=False))


class FeedbackManager:
    """Captures, correlates, stores, routes, and resolves feedback.

    Every collaborator is optional: ``FeedbackManager()`` keeps feedback in
    memory, correlates it by run, thread, or checkpoint, routes nothing, and
    logs observability events. Pass your own implementations to use
    production infrastructure. Instances share no state, so several managers
    (per tenant or per test) coexist in one process.

    Args:
        store: Persists feedback. Defaults to `InMemoryFeedbackStore`.
        router: Selects handlers for new feedback. Defaults to a
            `DefaultFeedbackRouter` without rules, which routes nothing.
        correlator: Assigns correlation IDs. Defaults to `DefaultFeedbackCorrelator`.
        provenance_provider: Resolves where new feedback came from, for
            example `AgxaiProvenanceProvider`. When given, new feedback gets a
            `FeedbackProvenanceReference` to the run it refers to.
        lifecycle_policy: Business rules checked before every transition.
        redaction_policy: Redacts new feedback before it is processed or stored.
        failure_policy: The failure mode of each processing stage. Defaults to
            best-effort for every stage.
        observability_sink: Receives observability events. Defaults to
            `LoggingObservabilitySink`.
        learning: Enable automatic, process-local learning by default. Artifacts
            remain candidates until explicitly reviewed and activated.
        learning_engine: Optional engine with an application-supplied artifact repository.
        learning_job_store: Optional durable queue. Defaults to in-memory jobs.
        learning_scope_resolver: Trusted scope resolver. By default feedback without
            a tenant is skipped and counted, never assigned a guessed tenant.
        learning_change_source: Optional atomic replay feed; inferred from a compatible store.
        require_durable_learning: Reject non-durable artifact/queue or missing
            change-feed providers.

    Raises:
        FeedbackConfigurationError: If a collaborator has the wrong type.
    """

    def __init__(
        self,
        *,
        store: FeedbackStore | None = None,
        router: FeedbackRouter | None = None,
        correlator: FeedbackCorrelator | None = None,
        provenance_provider: ProvenanceProvider | None = None,
        lifecycle_policy: FeedbackLifecyclePolicy | None = None,
        redaction_policy: FeedbackRedactionPolicy | None = None,
        failure_policy: FailurePolicy | None = None,
        observability_sink: ObservabilitySink | None = None,
        learning: bool = True,
        learning_engine: LearningEngine | None = None,
        learning_job_store: LearningJobStore | None = None,
        learning_scope_resolver: ScopeResolver
        | Callable[[FeedbackEvent], ArtifactScope | None]
        | None = None,
        learning_change_source: FeedbackChangeSource | None = None,
        require_durable_learning: bool = False,
    ) -> None:
        for value, expected, name in (
            (store, FeedbackStore, "store"),
            (router, FeedbackRouter, "router"),
            (correlator, FeedbackCorrelator, "correlator"),
            (provenance_provider, ProvenanceProvider, "provenance_provider"),
            (lifecycle_policy, FeedbackLifecyclePolicy, "lifecycle_policy"),
            (redaction_policy, FeedbackRedactionPolicy, "redaction_policy"),
            (failure_policy, FailurePolicy, "failure_policy"),
            (observability_sink, ObservabilitySink, "observability_sink"),
            (learning_engine, LearningEngine, "learning_engine"),
            (learning_job_store, LearningJobStore, "learning_job_store"),
            (learning_change_source, FeedbackChangeSource, "learning_change_source"),
        ):
            if value is not None and not isinstance(value, expected):
                raise FeedbackConfigurationError(
                    f"{name} must be a {expected.__name__}, not {type(value).__name__}"
                )
        if not isinstance(learning, bool) or not isinstance(require_durable_learning, bool):
            raise FeedbackConfigurationError("learning flags must be booleans")
        scope_resolver: ScopeResolver = ContextScopeResolver()
        if learning_scope_resolver is not None:
            try:
                scope_resolver = as_scope_resolver(learning_scope_resolver)
            except TypeError:
                raise FeedbackConfigurationError(
                    "learning_scope_resolver must be a ScopeResolver or a callable"
                ) from None
        self._store: FeedbackStore = store if store is not None else InMemoryFeedbackStore()
        self._router: FeedbackRouter = router if router is not None else DefaultFeedbackRouter()
        self._correlator: FeedbackCorrelator = (
            correlator if correlator is not None else DefaultFeedbackCorrelator()
        )
        self._provenance = provenance_provider
        self._lifecycle_policy = lifecycle_policy
        self._redaction_policy = redaction_policy
        self._failure_policy = failure_policy if failure_policy is not None else FailurePolicy()
        self._sink: ObservabilitySink = (
            observability_sink if observability_sink is not None else LoggingObservabilitySink()
        )
        self._lock = threading.Lock()
        self._subscribers: dict[object, FeedbackSubscriber] = {}
        self._streams: set[FeedbackStream] = set()
        if require_durable_learning and not learning:
            raise FeedbackConfigurationError("durable learning cannot be requested when disabled")
        self.learning_engine = (
            (
                learning_engine
                if learning_engine is not None
                else LearningEngine(InMemoryArtifactRepository())
            )
            if learning
            else None
        )
        self.background_learner = (
            BackgroundLearner(
                self,
                self.learning_engine,
                learning_job_store
                if learning_job_store is not None
                else InMemoryLearningJobStore(),
                scope_resolver=scope_resolver,
                change_source=(
                    learning_change_source
                    if learning_change_source is not None
                    else self._store
                    if isinstance(self._store, FeedbackChangeSource)
                    else None
                ),
                require_durable=require_durable_learning,
            )
            if self.learning_engine is not None
            else None
        )

    # -- submission ------------------------------------------------------------

    def for_tenant(self, tenant_id: str) -> TenantFeedbackManager:
        """Return an isolated facade for a tenant authenticated by the host."""
        return TenantFeedbackManager(self, tenant_id)

    async def submit(
        self,
        *,
        source: FeedbackSource | str,
        category: FeedbackCategory | str,
        target: FeedbackTarget,
        payload: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
        execution_context: ExecutionContext | None = None,
        idempotency_key: str | None = None,
        feedback_type: str | None = None,
    ) -> FeedbackEvent:
        """Submit new feedback.

        The feedback is redacted, correlated, linked to its provenance when a
        `ProvenanceProvider` is configured, stored as ``RECEIVED``, published to
        subscribers and streams, and routed to handlers. Correlation,
        provenance, routing, handler, and subscriber failures follow the
        `FailurePolicy`; a store failure always propagates.

        Args:
            source: Who or what produced the feedback.
            category: What kind of feedback it is.
            target: What the feedback is about.
            payload: The feedback content; JSON-compatible.
            metadata: Application-defined details; JSON-compatible.
            execution_context: The execution the target belongs to.
            idempotency_key: Deduplicates retries: a submission with a key that
                is already stored returns the stored event, in its current
                state, without processing it again.
            feedback_type: An optional, application-defined subtype.

        Returns:
            The stored event.

        Raises:
            FeedbackValidationError: If the input is invalid or redaction fails.
            FeedbackStoreError: If the store fails.
            FeedbackManagerError: The stage's error type if a ``BLOCKING`` stage fails.
        """
        event = _build_event(
            source=source,
            category=category,
            target=target,
            payload=payload,
            metadata=metadata,
            execution_context=execution_context,
            idempotency_key=idempotency_key,
            feedback_type=feedback_type,
        )
        tenant = None if event.execution_context is None else event.execution_context.tenant_id
        if event.idempotency_key is not None:
            replayed = await self._call_store(
                partial(
                    self._store.query,
                    FeedbackQuery(
                        idempotency_key=event.idempotency_key,
                        limit=1,
                        tenant_id=tenant,
                        unscoped_only=tenant is None,
                    ),
                ),
                event=event,
            )
            if replayed:
                self._check_submission_tenant(replayed[0], tenant)
                return replayed[0]
        event = self._redact(event)
        self._check_submission_tenant(event, tenant)
        on_error = partial(self._report_failure, event)
        correlation_id = await self._failure_policy.run_stage(
            FeedbackStage.CORRELATION,
            partial(self._correlate, event),
            feedback_id=event.feedback_id,
            on_error=on_error,
        )
        provenance = None
        if self._provenance is not None:
            provenance = await self._failure_policy.run_stage(
                FeedbackStage.PROVENANCE,
                partial(self._provenance.resolve, event),
                feedback_id=event.feedback_id,
                on_error=on_error,
            )
        received = event.model_copy(
            update={
                "correlation_id": correlation_id or default_correlation_id(event),
                "provenance": provenance,
                "status": FeedbackStatus.RECEIVED,
            }
        )
        stored = await self._call_store(partial(self._store.create, received), event=received)
        self._check_submission_tenant(stored, tenant)
        if stored.feedback_id != received.feedback_id:
            return stored
        self._emit(FEEDBACK_RECEIVED, stored)
        await self._publish(stored)
        await self._route(stored)
        return stored

    # -- lifecycle -------------------------------------------------------------

    async def acknowledge(self, feedback_id: UUID) -> FeedbackEvent:
        """Move feedback to ``ACKNOWLEDGED``: a consumer has seen it.

        Lifecycle methods are idempotent: moving an event to the status it is
        already in returns it unchanged and publishes nothing.

        Raises:
            FeedbackNotFoundError: If the feedback does not exist.
            FeedbackLifecycleError: If the move is illegal or denied by the
                lifecycle policy.
            FeedbackStoreError: If the store fails.
        """
        return await self._transition(feedback_id, FeedbackStatus.ACKNOWLEDGED)

    async def mark_handled(self, feedback_id: UUID) -> FeedbackEvent:
        """Move feedback to ``HANDLED``: processing finished and awaits a resolution.

        Raises the same errors as `acknowledge`.
        """
        return await self._transition(feedback_id, FeedbackStatus.HANDLED)

    async def resolve(
        self, feedback_id: UUID, *, resolution: Mapping[str, Any] | None = None
    ) -> FeedbackEvent:
        """Close ``HANDLED`` feedback as ``RESOLVED``.

        Args:
            feedback_id: The feedback to close.
            resolution: How it was resolved; JSON-compatible.

        Raises the same errors as `acknowledge`, and `FeedbackValidationError`
        if ``resolution`` is not JSON-compatible.
        """
        return await self._transition(
            feedback_id, FeedbackStatus.RESOLVED, resolution=_resolution(resolution, None)
        )

    async def reject(
        self,
        feedback_id: UUID,
        *,
        reason: str | None = None,
        resolution: Mapping[str, Any] | None = None,
    ) -> FeedbackEvent:
        """Close feedback as ``REJECTED``: declined or not applicable.

        Args:
            feedback_id: The feedback to close.
            reason: Shorthand for ``resolution={"reason": reason}``; added to
                ``resolution`` when both are given.
            resolution: Why it was rejected; JSON-compatible.

        Raises the same errors as `resolve`.
        """
        return await self._transition(
            feedback_id, FeedbackStatus.REJECTED, resolution=_resolution(resolution, reason)
        )

    async def cancel(
        self,
        feedback_id: UUID,
        *,
        reason: str | None = None,
        resolution: Mapping[str, Any] | None = None,
    ) -> FeedbackEvent:
        """Close feedback as ``CANCELLED``: withdrawn before completion.

        Takes the same arguments and raises the same errors as `reject`.
        """
        return await self._transition(
            feedback_id, FeedbackStatus.CANCELLED, resolution=_resolution(resolution, reason)
        )

    async def expire(self, feedback_id: UUID) -> FeedbackEvent:
        """Close pending feedback as ``EXPIRED``.

        Raises the same errors as `acknowledge`.
        """
        return await self._transition(feedback_id, FeedbackStatus.EXPIRED)

    async def expire_overdue(
        self, policy: RetentionPolicy, *, now: datetime | None = None
    ) -> list[FeedbackEvent]:
        """Expire every pending event that ``policy`` considers overdue.

        Call it periodically, for example from a scheduled job; several workers
        may run it at once. Events that change concurrently, including those
        another worker expires first, that are deleted, or that the lifecycle
        policy refuses to expire, are skipped.

        Args:
            policy: Decides which events are overdue.
            now: The current time (timezone-aware); defaults to now.

        Returns:
            The events this call expired.

        Raises:
            FeedbackValidationError: If ``now`` is naive.
            FeedbackStoreError: If the store fails.
        """
        cutoff = policy.cutoff(now)
        expired: list[FeedbackEvent] = []
        for status in _EXPIRABLE:
            for event in await self.query(FeedbackQuery(status=status, created_before=cutoff)):
                try:
                    updated, changed = await self._move(event.feedback_id, FeedbackStatus.EXPIRED)
                except (FeedbackLifecycleError, FeedbackNotFoundError):
                    continue
                if changed:
                    expired.append(updated)
        return expired

    # -- retrieval -------------------------------------------------------------

    async def get(self, feedback_id: UUID) -> FeedbackEvent | None:
        """Return the feedback with ``feedback_id``, or ``None``.

        Raises:
            FeedbackStoreError: If the store fails.
        """
        return await self._call_store(partial(self._store.get, feedback_id))

    async def query(self, query: FeedbackQuery | None = None) -> Sequence[FeedbackEvent]:
        """Return the feedback matching ``query``; every event when it is omitted.

        Raises:
            FeedbackStoreError: If the store fails.
        """
        return await self._call_store(
            partial(self._store.query, _EVERYTHING if query is None else query)
        )

    # -- push delivery ---------------------------------------------------------

    def subscribe(
        self,
        subscriber: FeedbackSubscriber | Callable[[FeedbackEvent], Awaitable[None]],
    ) -> Subscription:
        """Call ``subscriber`` with every new event and every lifecycle change.

        Subscribers run one after another in the task that changed the
        feedback; failures follow the ``SUBSCRIBER`` failure mode. A plain
        ``async def`` function is wrapped in `FunctionSubscriber`.

        Returns:
            A handle that stops the notifications when cancelled.

        Raises:
            FeedbackConfigurationError: If ``subscriber`` is not callable.
        """
        if not isinstance(subscriber, FeedbackSubscriber):
            if not callable(subscriber):
                raise FeedbackConfigurationError(
                    "subscriber must be a FeedbackSubscriber or an async callable"
                )
            subscriber = FunctionSubscriber(subscriber)
        token = object()
        with self._lock:
            self._subscribers[token] = subscriber
        return Subscription(partial(self._unsubscribe, token))

    def stream(self, query: FeedbackQuery | None = None) -> FeedbackStream:
        """Open a stream of the events published from now on that match ``query``.

        Raises:
            FeedbackConfigurationError: If called outside a running event loop.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            raise FeedbackConfigurationError(
                "stream() must be called from a running event loop"
            ) from None
        stream = FeedbackStream(loop, query, self._discard_stream)
        with self._lock:
            self._streams.add(stream)
        return stream

    async def aclose(self) -> None:
        """Close every open stream, so their consumers stop after the delivered events.

        Drain due learning jobs and stop automatic processing. The manager stays
        usable; submitting more feedback restarts learning. Collaborator stores
        are not closed: they belong to the application. Delayed retries remain
        queued for the next start.
        """
        with self._lock:
            streams = tuple(self._streams)
        for stream in streams:
            await stream.aclose()
        if self.background_learner is not None:
            await self.background_learner.stop()
            await self.background_learner.drain()

    async def __aenter__(self) -> Self:
        if (
            self.background_learner is not None
            and self.background_learner.change_source is not None
            and not self.background_learner.running
        ):
            await self.background_learner.start(subscribe=False)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    # -- internals -------------------------------------------------------------

    def _redact(self, event: FeedbackEvent) -> FeedbackEvent:
        if self._redaction_policy is None:
            return event
        try:
            redacted = self._redaction_policy.redact(event)
        except FeedbackManagerError:
            raise
        except Exception as exc:
            raise FeedbackValidationError(
                f"redaction policy failed: {type(exc).__name__}: {exc}",
                feedback_id=event.feedback_id,
            ) from exc
        if not isinstance(redacted, FeedbackEvent):
            raise FeedbackConfigurationError(
                f"redaction policy must return a FeedbackEvent, not {type(redacted).__name__}",
                feedback_id=event.feedback_id,
            )
        return redacted

    async def _correlate(self, event: FeedbackEvent) -> str:
        correlation_id = await self._correlator.correlate(event)
        if (
            not isinstance(correlation_id, str)
            or not correlation_id.strip()
            or correlation_id != correlation_id.strip()
        ):
            raise FeedbackValidationError(
                f"correlator returned an invalid correlation ID: {correlation_id!r}",
                feedback_id=event.feedback_id,
            )
        return correlation_id

    async def _transition(
        self,
        feedback_id: UUID,
        status: FeedbackStatus,
        *,
        resolution: JsonObject | None = None,
    ) -> FeedbackEvent:
        event, _ = await self._move(feedback_id, status, resolution=resolution)
        return event

    async def _move(
        self,
        feedback_id: UUID,
        status: FeedbackStatus,
        *,
        resolution: JsonObject | None = None,
    ) -> tuple[FeedbackEvent, bool]:
        """Move feedback to ``status``; also return whether this call changed it."""
        for _ in range(_TRANSITION_ATTEMPTS):
            current = await self.get(feedback_id)
            if current is None:
                raise FeedbackNotFoundError("unknown feedback event", feedback_id=feedback_id)
            if validate_transition(feedback_id, current.status, status).idempotent:
                return current, False
            self._authorize(current, status)
            try:
                updated = await self._call_store(
                    partial(
                        self._store.transition,
                        feedback_id,
                        status,
                        expected=current.status,
                        resolution=resolution,
                    ),
                    event=current,
                )
            except FeedbackConflictError:
                continue
            self._emit(_STATUS_EVENTS[status], updated)
            await self._publish(updated)
            return updated, True
        raise FeedbackConflictError(
            "feedback kept changing concurrently; transition not applied",
            feedback_id=feedback_id,
            requested_status=status.value,
        )

    def _authorize(self, current: FeedbackEvent, status: FeedbackStatus) -> None:
        if self._lifecycle_policy is None:
            return
        try:
            self._lifecycle_policy.authorize_transition(current, status)
        except FeedbackManagerError:
            raise
        except Exception as exc:
            raise FeedbackLifecycleError(
                f"lifecycle policy failed: {type(exc).__name__}: {exc}",
                feedback_id=current.feedback_id,
                current_status=current.status.value,
                requested_status=status.value,
            ) from exc

    async def _route(self, event: FeedbackEvent) -> None:
        on_error = partial(self._report_failure, event)
        handlers = await self._failure_policy.run_stage(
            FeedbackStage.ROUTING,
            partial(self._select_handlers, event),
            feedback_id=event.feedback_id,
            on_error=on_error,
        )
        if not handlers:
            return
        handled = 0
        for handler in handlers:
            if await self._failure_policy.run_stage(
                FeedbackStage.HANDLER,
                partial(_handle, handler, event),
                feedback_id=event.feedback_id,
                on_error=on_error,
            ):
                handled += 1
        self._emit(FEEDBACK_ROUTED, event, handler_count=len(handlers), handled_count=handled)

    async def _select_handlers(self, event: FeedbackEvent) -> tuple[FeedbackHandler, ...]:
        handlers = tuple(await self._router.route(event))
        for handler in handlers:
            if not isinstance(handler, FeedbackHandler):
                raise TypeError(
                    f"{type(self._router).__name__}.route returned "
                    f"{type(handler).__name__}, not a FeedbackHandler"
                )
        return handlers

    async def _publish(self, event: FeedbackEvent) -> None:
        if self.background_learner is not None:
            await self._failure_policy.run_stage(
                FeedbackStage.SUBSCRIBER,
                partial(self._enqueue_learning, event),
                feedback_id=event.feedback_id,
                on_error=partial(self._report_failure, event),
            )
        with self._lock:
            streams = tuple(self._streams)
            subscribers = tuple(self._subscribers.values())
        for stream in streams:
            stream._publish(event)
        on_error = partial(self._report_failure, event)
        for subscriber in subscribers:
            await self._failure_policy.run_stage(
                FeedbackStage.SUBSCRIBER,
                partial(subscriber, event),
                feedback_id=event.feedback_id,
                on_error=on_error,
            )

    async def _enqueue_learning(self, event: FeedbackEvent) -> None:
        assert self.background_learner is not None
        job = await self.background_learner.enqueue(event)
        if job is not None and not self.background_learner.running:
            await self.background_learner.start(subscribe=False)

    @staticmethod
    def _check_submission_tenant(event: FeedbackEvent, tenant: str | None) -> None:
        actual = None if event.execution_context is None else event.execution_context.tenant_id
        if actual != tenant:
            raise FeedbackStoreError("feedback store returned a record from another tenant")

    async def _call_store[T](
        self, operation: Callable[[], Awaitable[T]], *, event: FeedbackEvent | None = None
    ) -> T:
        try:
            return await operation()
        except _CALLER_ERRORS:
            raise
        except FeedbackManagerError as exc:
            if event is not None:
                self._report_failure(event, "store", exc)
            raise
        except Exception as exc:
            if event is not None:
                self._report_failure(event, "store", exc)
            raise FeedbackStoreError(
                f"feedback store failed: {type(exc).__name__}: {exc}",
                feedback_id=None if event is None else event.feedback_id,
            ) from exc

    def _report_failure(
        self, event: FeedbackEvent, stage: FeedbackStage | str, error: Exception
    ) -> None:
        self._emit(
            FEEDBACK_FAILED,
            event,
            stage=str(stage),
            error_type=type(error).__name__,
            error=str(error),
        )

    def _emit(self, name: str, event: FeedbackEvent, **attributes: Any) -> None:
        try:
            self._sink.emit(
                ObservabilityEvent(
                    name=name,
                    feedback_id=event.feedback_id,
                    attributes={
                        "source": str(event.source),
                        "category": str(event.category),
                        "target_type": str(event.target.type),
                        "status": event.status.value,
                        **attributes,
                    },
                )
            )
        except Exception as exc:
            _logger.warning(
                "observability sink failed",
                event_name=name,
                feedback_id=str(event.feedback_id),
                error_type=type(exc).__name__,
                exc_info=exc,
            )

    def _unsubscribe(self, token: object) -> None:
        with self._lock:
            self._subscribers.pop(token, None)

    def _discard_stream(self, stream: FeedbackStream) -> None:
        with self._lock:
            self._streams.discard(stream)


async def _handle(handler: FeedbackHandler, event: FeedbackEvent) -> bool:
    result = await handler.handle(event)
    if not isinstance(result, FeedbackHandlerResult):
        raise TypeError(
            f"{type(handler).__name__}.handle returned {type(result).__name__}, "
            "not a FeedbackHandlerResult"
        )
    return result.handled


def _build_event(**fields: Any) -> FeedbackEvent:
    for name in ("payload", "metadata"):
        if fields[name] is None:
            fields[name] = {}
    try:
        return FeedbackEvent(**fields)
    except ValidationError as exc:
        raise FeedbackValidationError(f"invalid feedback: {_describe(exc)}") from exc


def _resolution(resolution: Mapping[str, Any] | None, reason: str | None) -> JsonObject | None:
    if resolution is None and not reason:
        return None
    merged = dict(resolution or {})
    if reason:
        merged["reason"] = reason
    try:
        return _resolution_adapter().validate_python(merged)
    except ValidationError as exc:
        raise FeedbackValidationError(f"invalid resolution: {_describe(exc)}") from exc


def _describe(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc']) or 'value'}: {item['msg']}"
        for item in error.errors(include_url=False)
    )


__all__ = ["FeedbackManager"]
