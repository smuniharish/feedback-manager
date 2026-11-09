import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from feedback_manager.learning import ArtifactScope, LearningError
from feedback_manager.learning.jobs import (
    InMemoryLearningJobStore,
    LearningJob,
    LearningJobNotFoundError,
    LearningJobStatus,
    LearningLeaseLostError,
    SQLiteLearningJobStore,
)

SCOPE = ArtifactScope(tenant_id="tenant", agent_id="agent")


@pytest.fixture(params=["memory", "sqlite"])
async def store(request, tmp_path):
    value = (
        InMemoryLearningJobStore()
        if request.param == "memory"
        else SQLiteLearningJobStore(tmp_path / "jobs.db")
    )
    yield value
    await value.close()


async def test_job_lifecycle_and_deduplication(store):
    assert await store.checkpoint("source") is None
    await store.save_checkpoint("source", "position")
    assert await store.checkpoint("source") == "position"
    job = LearningJob.for_feedback(uuid4(), "received", SCOPE)
    assert job.job_id == LearningJob.for_feedback(job.feedback_id, "received", SCOPE).job_id
    assert await store.enqueue(job)
    assert not await store.enqueue(job)
    assert await store.get(job.job_id) == job
    assert await store.list() == (job,)
    assert await store.list(status=LearningJobStatus.RUNNING) == ()
    (claimed,) = await store.claim(limit=1, lease=timedelta(seconds=30))
    assert claimed.attempts == claimed.retry_attempt == 1
    assert claimed.status is LearningJobStatus.RUNNING
    assert not await store.claim(limit=1, lease=timedelta(seconds=30))
    counts = await store.counts()
    assert counts[LearningJobStatus.RUNNING] == 1
    assert counts[LearningJobStatus.SUCCEEDED] == 0
    done = await store.complete(job.job_id, attempt=claimed.attempts)
    assert done.status is LearningJobStatus.SUCCEEDED
    assert done.lease_expires_at is None
    with pytest.raises(LearningLeaseLostError):
        await store.complete(job.job_id, attempt=claimed.attempts)


async def test_retry_dead_letter_and_replay(store):
    job = LearningJob.for_feedback(uuid4(), "received", SCOPE)
    await store.enqueue(job)
    (first,) = await store.claim(limit=1, lease=timedelta(seconds=1))
    future = datetime.now(UTC) + timedelta(days=1)
    pending = await store.retry(
        job.job_id, attempt=first.attempts, error="x" * 3000, available_at=future
    )
    assert len(pending.last_error) == 2000
    assert not await store.claim(limit=1, lease=timedelta(seconds=1))
    (second,) = await store.claim(limit=1, lease=timedelta(seconds=1), now=future)
    assert second.attempts == 2
    dead = await store.dead_letter(job.job_id, attempt=second.attempts, error="failed")
    assert dead.status is LearningJobStatus.DEAD_LETTERED
    assert await store.list(status=LearningJobStatus.DEAD_LETTERED) == (dead,)
    assert not await store.claim(limit=1, lease=timedelta(seconds=1), now=future)
    requeued = await store.requeue(job.job_id)
    assert requeued.attempts == 2
    assert requeued.retry_attempt == 0
    (third,) = await store.claim(limit=1, lease=timedelta(seconds=1))
    assert third.attempts == 3
    assert third.retry_attempt == 1


async def test_expired_lease_is_reclaimed_and_fenced(store):
    job = LearningJob.for_feedback(uuid4(), "received", SCOPE)
    await store.enqueue(job)
    (first,) = await store.claim(limit=1, lease=timedelta(seconds=1))
    (second,) = await store.claim(limit=1, lease=timedelta(seconds=1), now=first.lease_expires_at)
    assert second.attempts == 2
    with pytest.raises(LearningLeaseLostError):
        await store.complete(first.job_id, attempt=first.attempts)
    await store.complete(second.job_id, attempt=second.attempts)


async def test_atomic_claims(store):
    for _ in range(10):
        await store.enqueue(LearningJob.for_feedback(uuid4(), "received", SCOPE))
    claims = await asyncio.gather(
        *(store.claim(limit=10, lease=timedelta(minutes=1)) for _ in range(3))
    )
    ids = [job.job_id for batch in claims for job in batch]
    assert len(ids) == len(set(ids)) == 10


async def test_errors(store):
    missing = uuid4()
    with pytest.raises(LearningJobNotFoundError):
        await store.get(missing)
    with pytest.raises(LearningJobNotFoundError):
        await store.complete(missing, attempt=1)
    with pytest.raises(ValueError, match="limit"):
        await store.list(limit=0)
    with pytest.raises(ValueError, match="limit"):
        await store.claim(limit=False, lease=timedelta(seconds=1))
    with pytest.raises(ValueError, match="lease"):
        await store.claim(limit=1, lease=timedelta(0))
    with pytest.raises(ValueError, match="timezone"):
        await store.claim(
            limit=1,
            lease=timedelta(seconds=1),
            now=datetime(2020, 1, 1, tzinfo=None),  # noqa: DTZ001
        )
    job = LearningJob.for_feedback(uuid4(), "received", SCOPE)
    await store.enqueue(job)
    with pytest.raises(LearningError, match="only dead"):
        await store.requeue(job.job_id)
    await store.claim(limit=1, lease=timedelta(seconds=1))
    with pytest.raises(ValueError, match="timezone"):
        await store.retry(
            job.job_id,
            attempt=1,
            error="x",
            available_at=datetime(2020, 1, 1, tzinfo=None),  # noqa: DTZ001
        )


async def test_sqlite_restart_and_cross_connection_claim(tmp_path):
    path = tmp_path / "durable.db"
    first = SQLiteLearningJobStore(path)
    job = LearningJob.for_feedback(uuid4(), "received", SCOPE)
    await first.enqueue(job)
    (claimed,) = await first.claim(limit=1, lease=timedelta(seconds=1))
    await first.close()
    second, third = SQLiteLearningJobStore(path), SQLiteLearningJobStore(path)
    try:
        assert await second.get(job.job_id) == claimed
        batches = await asyncio.gather(
            *(
                store.claim(limit=1, lease=timedelta(seconds=1), now=claimed.lease_expires_at)
                for store in (second, third)
            )
        )
        assert sum(len(batch) for batch in batches) == 1
        assert (await third.counts())[LearningJobStatus.RUNNING] == 1
    finally:
        await second.close()
        await third.close()
