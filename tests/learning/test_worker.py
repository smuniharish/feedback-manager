import asyncio
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest

from feedback_manager import ExecutionContext, FeedbackManager
from feedback_manager.core.events import FeedbackEvent
from feedback_manager.core.status import FeedbackStatus
from feedback_manager.core.targets import FeedbackTarget, FeedbackTargetType
from feedback_manager.learning import ArtifactScope, LearningScopeError, ScopeVisibility
from feedback_manager.learning.engine import LearningEngine
from feedback_manager.learning.jobs import (
    InMemoryLearningJobStore,
    LearningJob,
    LearningJobStatus,
)
from feedback_manager.learning.worker import BackgroundLearner, ContextScopeResolver, RetryPolicy
from feedback_manager.storage.artifacts import InMemoryArtifactRepository

SCOPE = ArtifactScope(tenant_id="tenant", agent_id="agent")
TARGET = FeedbackTarget(type=FeedbackTargetType.RUN, id="run")


def worker(**kwargs):
    manager = FeedbackManager(learning=False)
    engine = LearningEngine(InMemoryArtifactRepository())
    learner = BackgroundLearner(
        manager, engine, InMemoryLearningJobStore(), scope_resolver=ContextScopeResolver(), **kwargs
    )
    return manager, engine, learner


async def submit(manager, **kwargs):
    return await manager.submit(
        source="human",
        category="correction",
        target=TARGET,
        payload={"correction": "Validate tool results"},
        execution_context=ExecutionContext(tenant_id="tenant", agent_id="agent", run_id="run"),
        **kwargs,
    )


def test_scope_resolution():
    event = FeedbackEvent(source="human", category="correction", target=TARGET)
    assert ContextScopeResolver()(event) is None
    shared = ContextScopeResolver(default_tenant="default")(event)
    assert shared is not None
    assert shared.visibility is ScopeVisibility.TENANT
    scoped = event.model_copy(
        update={"execution_context": ExecutionContext(tenant_id="tenant", agent_id="agent")}
    )
    assert ContextScopeResolver()(scoped) == SCOPE
    tenant = event.model_copy(update={"execution_context": ExecutionContext(tenant_id="tenant")})
    resolved = ContextScopeResolver()(tenant)
    assert resolved is not None
    assert resolved.tenant_id == "tenant"


def test_retry_policy():
    for build in (
        lambda: RetryPolicy(max_attempts=0),
        lambda: RetryPolicy(initial_delay=timedelta(seconds=-1)),
        lambda: RetryPolicy(max_delay=timedelta(0)),
        lambda: RetryPolicy(multiplier=0),
        lambda: RetryPolicy(jitter=2),
    ):
        with pytest.raises(ValueError, match=r"must|delays"):
            build()
    policy = RetryPolicy(jitter=0, max_delay=timedelta(seconds=5))
    assert policy.delay(1) == timedelta(seconds=1)
    assert policy.delay(3) == timedelta(seconds=4)
    assert policy.delay(10000) == timedelta(seconds=5)


def test_worker_configuration():
    for options in ({"batch_size": 0}, {"lease": timedelta(0)}, {"poll_interval": 0}):
        with pytest.raises(ValueError, match="must"):
            worker(**options)


async def test_manual_drain_reconciliation_and_duplicate_jobs():
    manager, engine, learner = worker()
    event = await submit(manager)
    job = await learner.enqueue(event)
    assert job is not None
    await learner(event)
    assert learner.stats.duplicates == 1
    assert await learner.reconcile() == 0
    assert await learner.drain() == 1
    assert learner.stats.learned == learner.stats.succeeded == 1
    stored = await learner.store.get(job.job_id)
    assert stored.status is LearningJobStatus.SUCCEEDED
    assert await learner.drain() == 0
    assert not learner.running
    await learner.stop()
    assert engine.repository is not None


async def test_reconcile_missed_notification_and_invalidation():
    manager, engine, learner = worker()
    event = await submit(manager)
    assert await learner.reconcile() == 1
    await learner.drain()
    rejected = await manager.reject(event.feedback_id, reason="incorrect")
    await learner.enqueue(rejected)
    with patch.object(
        engine, "invalidate_feedback", wraps=engine.invalidate_feedback
    ) as invalidate:
        await learner.drain()
        invalidate.assert_awaited_once()
    assert learner.stats.invalidated == 1


async def test_skipped_and_ignored():
    manager, _, learner = worker()
    raw = FeedbackEvent(source="human", category="correction", target=TARGET)
    assert await learner.enqueue(raw) is None
    assert await learner.enqueue(raw.model_copy(update={"status": FeedbackStatus.RECEIVED})) is None
    assert learner.stats.skipped == 2
    event = await manager.submit(
        source="human",
        category="rating",
        target=TARGET,
        execution_context=ExecutionContext(tenant_id="tenant"),
    )
    await learner.enqueue(event)
    await learner.drain()
    assert learner.stats.ignored == 1


async def test_retry_exhaustion_and_replay():
    manager, engine, learner = worker(
        retry=RetryPolicy(max_attempts=2, initial_delay=timedelta(0), jitter=0)
    )
    event = await submit(manager)
    job = await learner.enqueue(event)
    assert job is not None
    with patch.object(engine, "process", side_effect=OSError("sensitive content")):
        assert await learner.drain() == 2
    assert learner.stats.retried == learner.stats.dead_lettered == 1
    dead = await learner.store.get(job.job_id)
    assert dead.last_error == "OSError"
    assert await learner.replay_dead_letters() == 1
    assert await learner.drain() == 1
    assert await learner.replay_dead_letters() == 0


async def test_permanent_failure_and_missing_feedback():
    manager, engine, learner = worker()
    event = await submit(manager)
    job = await learner.enqueue(event)
    assert job is not None
    with patch.object(engine, "process", side_effect=LearningScopeError("wrong scope")):
        assert await learner.drain() == 1
    assert (await learner.store.get(job.job_id)).status is LearningJobStatus.DEAD_LETTERED
    missing = LearningJob.for_feedback(uuid4(), "received", SCOPE)
    await learner.store.enqueue(missing)
    await learner.drain()
    assert (await learner.store.get(missing.job_id)).status is LearningJobStatus.DEAD_LETTERED


async def test_background_subscription_and_shutdown():
    manager, _, learner = worker(poll_interval=0.005)
    done = asyncio.Event()
    complete = learner.store.complete

    async def completed(*args, **kwargs):
        result = await complete(*args, **kwargs)
        done.set()
        return result

    with patch.object(learner.store, "complete", side_effect=completed):
        async with learner:
            assert learner.running
            with pytest.raises(RuntimeError, match="already running"):
                await learner.start()
            await submit(manager)
            await asyncio.wait_for(done.wait(), 5)
    assert not learner.running
    await learner.start(subscribe=False, reconcile=True)
    await learner.stop()


async def test_worker_survives_queue_outage(caplog):
    _, _, learner = worker(poll_interval=0.005)
    with patch.object(learner, "run_once", side_effect=[OSError("offline"), 0, 0, 0]):
        await learner.start(subscribe=False)
        await asyncio.sleep(0.012)
        await learner.stop()
    assert "learning worker batch failed" in caplog.text


async def test_interrupted_job_recovers_after_lease():
    manager, engine, learner = worker()
    event = await submit(manager)
    job = await learner.enqueue(event)
    assert job is not None
    entered = asyncio.Event()

    async def blocked(_signals):
        entered.set()
        await asyncio.Event().wait()

    with patch.object(engine, "process", side_effect=blocked):
        await learner.start(subscribe=False)
        await entered.wait()
        await learner.stop(cancel=True)
    running = await learner.store.get(job.job_id)
    assert running.status is LearningJobStatus.RUNNING
    expiry = running.lease_expires_at
    assert expiry is not None
    learner._clock = lambda: expiry + timedelta(seconds=1)
    assert await learner.drain() == 1
    assert (await learner.store.get(job.job_id)).status is LearningJobStatus.SUCCEEDED


async def test_draining_is_bounded():
    manager, _, learner = worker(batch_size=1)
    await learner.enqueue(await submit(manager))
    assert await learner.drain(max_batches=1) == 1
    assert await learner.drain(max_batches=0) == 0


async def test_default_manager_learns_and_can_disable():
    async with FeedbackManager() as manager:
        event = await submit(manager)
        assert manager.background_learner is not None
        assert manager.learning_engine is not None
        await manager.background_learner.drain()
        assert manager.background_learner.stats.succeeded == 1
        assert event.payload == {"correction": "Validate tool results"}
    assert not manager.background_learner.running
    await submit(manager)
    assert manager.background_learner.running
    await manager.aclose()
    disabled = FeedbackManager(learning=False)
    assert disabled.background_learner is disabled.learning_engine is None
    await disabled.aclose()


async def test_default_manager_does_not_guess_tenant():
    async with FeedbackManager() as manager:
        await manager.submit(source="human", category="rating", target=TARGET)
        assert manager.background_learner is not None
        assert manager.background_learner.stats.skipped == 1
        assert not manager.background_learner.running


async def test_recovery_requires_source_and_bounded_valid_pages():
    from feedback_manager.contracts.changes import FeedbackChangePage
    from feedback_manager.learning import LearningValidationError, UnsupportedCapabilityError

    manager, _, learner = worker(batch_size=1)
    with pytest.raises(UnsupportedCapabilityError, match="FeedbackChangeSource"):
        await learner.recover()
    from unittest.mock import AsyncMock

    source = AsyncMock()
    learner.change_source = source
    source.read_changes.return_value = FeedbackChangePage(events=(), cursor=None)
    assert await learner.recover() == 0
    with pytest.raises(ValueError, match="positive"):
        await learner.recover(max_pages=0)
    event = await submit(manager)
    source.read_changes.return_value = FeedbackChangePage(
        events=(event,), cursor="page-1", has_more=True
    )
    assert await learner.recover(max_pages=1) == 1
    await learner.store.save_checkpoint("default", "1")
    for page in (
        FeedbackChangePage(events=(event, event), cursor="1"),
        FeedbackChangePage(events=(event,), cursor=None),
        FeedbackChangePage(events=(), cursor=None, has_more=True),
        FeedbackChangePage(events=(), cursor="skip"),
    ):
        source.read_changes.return_value = page
        with pytest.raises(LearningValidationError, match="invalid page"):
            await learner.recover()
    source.read_changes.side_effect = [
        FeedbackChangePage(events=(event,), cursor="1", has_more=True),
        FeedbackChangePage(events=(), cursor="1"),
    ]
    await learner.store.save_checkpoint("default", "initial")
    assert await learner.recover() == 0
    source.read_changes.side_effect = None
    source.read_changes.return_value = FeedbackChangePage(events=(event,), cursor="1")
    with pytest.raises(LearningValidationError, match="invalid page"):
        await learner.recover()
    source.read_changes.return_value = FeedbackChangePage(events=(), cursor="1")
    read = asyncio.Event()
    original = source.read_changes

    async def observed(**kwargs):
        read.set()
        return await original(**kwargs)

    with patch.object(source, "read_changes", side_effect=observed):
        await learner.start(subscribe=False)
        await asyncio.wait_for(read.wait(), 5)
        await learner.stop()


async def test_stale_worker_failure_cannot_overwrite_new_claim():
    from feedback_manager.learning.jobs import LearningLeaseLostError

    manager, engine, learner = worker()
    await learner.enqueue(await submit(manager))
    with (
        patch.object(engine, "process", side_effect=LearningScopeError("invalid")),
        patch.object(learner.store, "dead_letter", side_effect=LearningLeaseLostError("stale")),
    ):
        await learner.run_once()


@pytest.mark.parametrize(
    "options",
    [
        {"learning": "yes"},
        {"learning_scope_resolver": 1},
        {"learning_engine": "engine"},
        {"learning_job_store": "jobs"},
        {"learning_change_source": "source"},
    ],
)
def test_learning_configuration_types_are_checked(options):
    from feedback_manager.errors import FeedbackConfigurationError

    with pytest.raises(FeedbackConfigurationError, match=r"must|flags"):
        FeedbackManager(**options)
