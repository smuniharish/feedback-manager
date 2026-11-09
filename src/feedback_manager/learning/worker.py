"""Automatic background learning from feedback lifecycle changes.

`BackgroundLearner` subscribes to a `FeedbackManager`, records one durable job per
learnable feedback state, and processes jobs in a supervised asyncio task with
retries, exponential backoff, and a dead-letter state. `recover` replays an atomic
feedback journal; `reconcile` is only a best-effort scan of current snapshots.
"""

from __future__ import annotations

import asyncio
import contextlib
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Self

from pydantic import ValidationError

from feedback_manager._logging import get_logger
from feedback_manager.contracts.subscriber import FeedbackSubscriber
from feedback_manager.core.status import FeedbackStatus
from feedback_manager.learning.errors import (
    LearningScopeError,
    LearningValidationError,
    UnsupportedCapabilityError,
)
from feedback_manager.learning.jobs import LearningJob, LearningJobStatus, LearningLeaseLostError
from feedback_manager.learning.models import ArtifactScope, ScopeVisibility
from feedback_manager.learning.signals import ExtractionDisposition, LearningSignalExtractor
from feedback_manager.observability.operations import operation

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import TracebackType

    from feedback_manager.api.manager import FeedbackManager
    from feedback_manager.api.subscription import Subscription
    from feedback_manager.contracts.changes import FeedbackChangeSource
    from feedback_manager.contracts.store import FeedbackQuery
    from feedback_manager.core.events import FeedbackEvent
    from feedback_manager.learning.engine import LearningEngine
    from feedback_manager.learning.jobs import LearningJobStore

_logger = get_logger("feedback_manager.learning.worker")

_INVALIDATING = frozenset(
    {FeedbackStatus.REJECTED, FeedbackStatus.CANCELLED, FeedbackStatus.EXPIRED}
)
_PERMANENT_ERRORS: tuple[type[BaseException], ...] = (
    LearningScopeError,
    LearningValidationError,
    ValidationError,
)


class ScopeResolver(ABC):
    """Map feedback to the scope it is learned into, or ``None`` to skip it.

    The resolver is the host's trust boundary: it decides which tenant, namespace,
    and agent a piece of feedback may teach. Plain functions are accepted wherever
    a resolver is expected and are wrapped in `FunctionScopeResolver`.
    """

    __slots__ = ()

    @abstractmethod
    def __call__(self, feedback: FeedbackEvent) -> ArtifactScope | None:
        """Return the learning scope for ``feedback``."""


class FunctionScopeResolver(ScopeResolver):
    """Adapt a plain function to `ScopeResolver`.

    Args:
        function: Called with each feedback event; returns its scope or ``None``.
    """

    __slots__ = ("function",)

    def __init__(self, function: Callable[[FeedbackEvent], ArtifactScope | None]) -> None:
        self.function = function

    def __call__(self, feedback: FeedbackEvent) -> ArtifactScope | None:
        """Forward ``feedback`` to the wrapped function."""
        return self.function(feedback)

    def __repr__(self) -> str:
        return f"FunctionScopeResolver({self.function!r})"


def as_scope_resolver(
    resolver: ScopeResolver | Callable[[FeedbackEvent], ArtifactScope | None],
) -> ScopeResolver:
    """Return ``resolver`` as a `ScopeResolver`, wrapping plain functions.

    Raises:
        TypeError: If ``resolver`` is neither a `ScopeResolver` nor callable.
    """
    if isinstance(resolver, ScopeResolver):
        return resolver
    if not callable(resolver):
        raise TypeError("scope_resolver must be a ScopeResolver or a callable")
    return FunctionScopeResolver(resolver)


@dataclass(frozen=True, slots=True)
class ContextScopeResolver(ScopeResolver):
    """Derive the scope from the feedback's execution context.

    Feedback with an ``agent_id`` is learned privately for that agent; feedback
    without one is shared tenant-wide. Feedback without a tenant uses
    ``default_tenant``, or is skipped when that is ``None``.
    """

    default_tenant: str | None = None
    namespace: str = "default"

    def __call__(self, feedback: FeedbackEvent) -> ArtifactScope | None:
        """Resolve feedback into a tenant and optional private agent scope."""
        context = feedback.execution_context
        tenant = (context.tenant_id if context is not None else None) or self.default_tenant
        if tenant is None:
            return None
        agent_id = context.agent_id if context is not None else None
        if agent_id is not None:
            return ArtifactScope(
                tenant_id=tenant,
                namespace=self.namespace,
                visibility=ScopeVisibility.AGENT,
                agent_id=agent_id,
            )
        return ArtifactScope(
            tenant_id=tenant, namespace=self.namespace, visibility=ScopeVisibility.TENANT
        )


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Exponential backoff for failed jobs.

    Attributes:
        max_attempts: Attempts before a job is dead-lettered.
        initial_delay: Delay after the first failure.
        max_delay: Upper bound on any delay.
        multiplier: Growth factor per attempt.
        jitter: Random extra delay, as a fraction of the computed delay.
    """

    max_attempts: int = 5
    initial_delay: timedelta = timedelta(seconds=1)
    max_delay: timedelta = timedelta(minutes=5)
    multiplier: float = 2.0
    jitter: float = 0.1

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.initial_delay < timedelta(0) or self.max_delay < self.initial_delay:
            raise ValueError("delays must be non-negative and max_delay >= initial_delay")
        if self.multiplier < 1 or not 0 <= self.jitter <= 1:
            raise ValueError("multiplier must be >= 1 and jitter between 0 and 1")

    def delay(self, attempt: int) -> timedelta:
        """Return the delay before retrying after failed attempt number ``attempt``."""
        seconds = self.initial_delay.total_seconds()
        maximum = self.max_delay.total_seconds()
        for _ in range(max(attempt - 1, 0)):
            seconds = min(seconds * self.multiplier, maximum)
            if seconds == maximum:
                break
        bounded = timedelta(seconds=seconds)
        return min(
            bounded + bounded * (random.random() * self.jitter),  # noqa: S311
            self.max_delay,
        )


@dataclass(slots=True)
class LearningWorkerStats:
    """Counters since the worker was created."""

    enqueued: int = 0
    duplicates: int = 0
    skipped: int = 0
    succeeded: int = 0
    learned: int = 0
    ignored: int = 0
    invalidated: int = 0
    retried: int = 0
    dead_lettered: int = 0
    errors: dict[str, int] = field(default_factory=dict)


class BackgroundLearner(FeedbackSubscriber):
    """Learn from feedback automatically, durably, and idempotently.

    Example:
        ```python
        learner = BackgroundLearner(
            manager,
            engine,
            SQLiteLearningJobStore("jobs.db"),
            scope_resolver=ContextScopeResolver(),
        )
        async with learner:  # subscribes and starts the worker
            await manager.submit(...)
        ```

    Learned artifacts are still candidates: publication remains an explicit review.
    """

    def __init__(
        self,
        manager: FeedbackManager,
        engine: LearningEngine,
        store: LearningJobStore,
        *,
        scope_resolver: ScopeResolver | Callable[[FeedbackEvent], ArtifactScope | None],
        extractor: LearningSignalExtractor | None = None,
        retry: RetryPolicy | None = None,
        batch_size: int = 10,
        lease: timedelta = timedelta(minutes=1),
        poll_interval: float = 1.0,
        clock: Callable[[], datetime] | None = None,
        change_source: FeedbackChangeSource | None = None,
        checkpoint_key: str = "default",
        require_durable: bool = False,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        if lease <= timedelta(0) or poll_interval <= 0:
            raise ValueError("lease and poll_interval must be positive")
        if require_durable and (
            not store.durable or not engine.repository.durable or change_source is None
        ):
            raise UnsupportedCapabilityError(
                "durable learning requires a durable artifact repository, job store "
                "and atomic feedback change source"
            )
        self.manager = manager
        self.engine = engine
        self.store = store
        self.scope_resolver = as_scope_resolver(scope_resolver)
        self.extractor = extractor or LearningSignalExtractor()
        self.retry = retry or RetryPolicy()
        self.batch_size = batch_size
        self.lease = lease
        self.poll_interval = poll_interval
        self.stats = LearningWorkerStats()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._subscription: Subscription | None = None
        self._processing = asyncio.Lock()
        self._stopping = False
        self.change_source = change_source
        self.checkpoint_key = checkpoint_key
        self._recovery = asyncio.Lock()

    @property
    def running(self) -> bool:
        """Whether the worker task is running."""
        return self._task is not None and not self._task.done()

    async def __call__(self, feedback: FeedbackEvent) -> None:
        """Subscriber entry point: record a job for ``feedback``."""
        await self.enqueue(feedback)

    async def enqueue(self, feedback: FeedbackEvent) -> LearningJob | None:
        """Record a job for the feedback's current state; return it, or ``None`` if skipped."""
        if feedback.status is FeedbackStatus.CREATED:
            self.stats.skipped += 1
            return None
        scope = self.scope_resolver(feedback)
        if scope is None:
            self.stats.skipped += 1
            _logger.info(
                "feedback quarantined from learning: no trusted scope",
                feedback_id=str(feedback.feedback_id),
            )
            return None
        job = LearningJob.for_feedback(feedback.feedback_id, feedback.status, scope)
        if await self.store.enqueue(job):
            self.stats.enqueued += 1
            self._wake.set()
        else:
            self.stats.duplicates += 1
        return job

    async def reconcile(self, query: FeedbackQuery | None = None) -> int:
        """Enqueue jobs for stored feedback matching ``query``; return how many were new.

        Safe to run repeatedly (for example at startup or on a schedule): jobs
        that already exist are not duplicated.
        """
        before = self.stats.enqueued
        for feedback in await self.manager.query(query):
            await self.enqueue(feedback)
        return self.stats.enqueued - before

    async def replay_dead_letters(self, *, limit: int = 100) -> int:
        """Return up to ``limit`` dead-lettered jobs to the queue; return how many."""
        jobs = await self.store.list(status=LearningJobStatus.DEAD_LETTERED, limit=limit)
        for job in jobs:
            await self.store.requeue(job.job_id)
        if jobs:
            self._wake.set()
        return len(jobs)

    async def recover(self, *, max_pages: int = 1000) -> int:
        """Replay a durable change feed; checkpoint only after all page jobs are saved.

        A crash before checkpointing replays duplicates safely. Source and queue
        must both be durable for crash recovery; the artifact repository must
        also be durable to preserve completed learning. No distributed transaction
        or exactly-once processing is claimed.
        """
        if self.change_source is None:
            raise UnsupportedCapabilityError("recovery requires a FeedbackChangeSource")
        if max_pages < 1:
            raise ValueError("max_pages must be positive")
        async with self._recovery:
            cursor = await self.store.checkpoint(self.checkpoint_key)
            before = self.stats.enqueued
            for _ in range(max_pages):
                page = await self.change_source.read_changes(after=cursor, limit=self.batch_size)
                if (
                    len(page.events) > self.batch_size
                    or (page.events and (page.cursor is None or page.cursor == cursor))
                    or (page.has_more and not page.events)
                    or (not page.events and page.cursor != cursor)
                ):
                    raise LearningValidationError("change source returned an invalid page")
                for event in page.events:
                    await self.enqueue(event)
                if page.cursor is not None:
                    await self.store.save_checkpoint(self.checkpoint_key, page.cursor)
                cursor = page.cursor
                if not page.has_more:
                    break
            return self.stats.enqueued - before

    async def run_once(self) -> int:
        """Claim and process one batch of due jobs; return how many were processed."""
        async with self._processing:
            count = 0
            for _ in range(self.batch_size):
                jobs = await self.store.claim(limit=1, lease=self.lease, now=self._clock())
                if not jobs:
                    break
                await self._process(jobs[0])
                count += 1
            return count

    async def drain(self, *, max_batches: int = 1000) -> int:
        """Process due jobs until none remain or ``max_batches`` is reached."""
        total = 0
        for _ in range(max_batches):
            processed = await self.run_once()
            if processed == 0:
                break
            total += processed
        return total

    async def _process(self, job: LearningJob) -> None:
        try:
            await self._learn(job)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            try:
                await self._fail(job, exc)
            except LearningLeaseLostError:
                _logger.warning("learning job lease lost", job_id=str(job.job_id))
        else:
            await self.store.complete(job.job_id, attempt=job.attempts)
            self.stats.succeeded += 1

    async def _learn(self, job: LearningJob) -> None:
        with operation(self.engine.observer, "learning.job"):
            await self._learn_feedback(job)

    async def _learn_feedback(self, job: LearningJob) -> None:
        feedback = await self.manager.get(job.feedback_id)
        if feedback is None:
            raise LearningValidationError(f"feedback {job.feedback_id} no longer exists")
        extraction = self.extractor.extract(feedback, scope=job.scope)
        if extraction.disposition is ExtractionDisposition.EXTRACTED:
            assert extraction.signal is not None
            await self.engine.process([extraction.signal])
            self.stats.learned += 1
        elif feedback.status in _INVALIDATING:
            await self.engine.invalidate_feedback(job.scope, feedback.feedback_id)
            self.stats.invalidated += 1
        else:
            self.stats.ignored += 1

    async def _fail(self, job: LearningJob, exc: Exception) -> None:
        name = type(exc).__name__
        self.stats.errors[name] = self.stats.errors.get(name, 0) + 1
        error = name
        if isinstance(exc, _PERMANENT_ERRORS) or job.retry_attempt >= self.retry.max_attempts:
            await self.store.dead_letter(job.job_id, attempt=job.attempts, error=error)
            self.stats.dead_lettered += 1
            _logger.warning(
                "learning job dead-lettered",
                job_id=str(job.job_id),
                feedback_id=str(job.feedback_id),
                attempts=job.attempts,
                error=name,
            )
            return
        available_at = self._clock() + self.retry.delay(job.retry_attempt)
        await self.store.retry(
            job.job_id, attempt=job.attempts, error=error, available_at=available_at
        )
        self.stats.retried += 1
        _logger.info(
            "learning job scheduled for retry",
            job_id=str(job.job_id),
            attempts=job.attempts,
            error=name,
        )

    async def start(self, *, subscribe: bool = True, reconcile: bool = False) -> None:
        """Start the worker task; optionally subscribe to the manager and reconcile first."""
        if self.running:
            raise RuntimeError("the background learner is already running")
        if reconcile:
            await self.reconcile()
        if subscribe and self._subscription is None:
            self._subscription = self.manager.subscribe(self)
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name="feedback-manager-learning")

    async def stop(self, *, cancel: bool = False) -> None:
        """Stop after the current batch, or cancel and recover through lease expiry."""
        if self._subscription is not None:
            self._subscription.cancel()
            self._subscription = None
        task, self._task = self._task, None
        if task is not None:
            self._stopping = True
            self._wake.set()
            if cancel:
                task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _run(self) -> None:
        while not self._stopping:
            self._wake.clear()
            try:
                if self.change_source is not None:
                    await self.recover(max_pages=1)
                processed = await self.run_once()
            except Exception as exc:
                _logger.error("learning worker batch failed", error=type(exc).__name__)
                processed = 0
            if processed == 0:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), self.poll_interval)

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.stop()


__all__ = [
    "BackgroundLearner",
    "ContextScopeResolver",
    "FunctionScopeResolver",
    "LearningWorkerStats",
    "RetryPolicy",
    "ScopeResolver",
]
