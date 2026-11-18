"""The PostgreSQL learning job store example against the job store contract.

Runs when ``FEEDBACK_MANAGER_TEST_POSTGRES_DSN`` is set. Each test uses its own
table prefix and drops its tables afterwards. Two store instances on separate
pools stand in for two hosts.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from feedback_manager.learning import ArtifactScope
from feedback_manager.learning.jobs import LearningJob
from feedback_manager.testing import LearningJobStoreContract

DSN = os.environ.get("FEEDBACK_MANAGER_TEST_POSTGRES_DSN", "")

pytestmark = [
    pytest.mark.examples,
    pytest.mark.postgres,
    pytest.mark.skipif(not DSN, reason="set FEEDBACK_MANAGER_TEST_POSTGRES_DSN"),
]

pytest.importorskip("psycopg", reason="needs the examples dependency group")
from postgres_learning_job_store import PostgresLearningJobStore  # noqa: E402

SCOPE = ArtifactScope(tenant_id="tenant-a", namespace="default", agent_id="researcher")


def _table() -> str:
    return f"jobs_test_{uuid4().hex[:12]}"


def run(scenario):
    """psycopg's async mode needs a selector loop, which is not the Windows default."""
    asyncio.run(scenario, loop_factory=asyncio.SelectorEventLoop)


class TestPostgresLearningJobStore(LearningJobStoreContract):
    def run_scenario(self, scenario):
        run(scenario)

    async def make_job_store(self):
        return await PostgresLearningJobStore.open(DSN, table=_table(), max_connections=5)

    async def release(self, component):
        await component.drop_schema()
        await component.close()


def test_concurrent_claims_across_instances_never_lease_a_job_twice() -> None:
    """Two hosts claiming in parallel split the due jobs without overlap."""

    async def scenario() -> None:
        table = _table()
        first = await PostgresLearningJobStore.open(DSN, table=table, max_connections=5)
        second = await PostgresLearningJobStore.open(DSN, table=table, max_connections=5)
        try:
            for _ in range(40):
                await first.enqueue(LearningJob.for_feedback(uuid4(), "received", SCOPE))
            now = datetime.now(UTC)
            claimed: list[LearningJob] = []
            while True:
                batch = await asyncio.gather(
                    first.claim(limit=3, lease=timedelta(minutes=5), now=now),
                    second.claim(limit=3, lease=timedelta(minutes=5), now=now),
                )
                if not any(batch):
                    break
                claimed.extend(job for part in batch for job in part)
            ids = [job.job_id for job in claimed]
            assert len(ids) == len(set(ids)) == 40
        finally:
            await first.drop_schema()
            await first.close()
            await second.close()

    run(scenario())


def test_example_self_check_runs_against_the_database(capsys: pytest.CaptureFixture[str]) -> None:
    """The example's own demo completes four jobs across two stores."""
    from postgres_learning_job_store import run as demo

    run(demo(DSN))
    assert "claimed 4 jobs across two store instances" in capsys.readouterr().out
