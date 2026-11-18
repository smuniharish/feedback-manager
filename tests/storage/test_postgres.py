"""PostgreSQL stores: run with ``FEEDBACK_MANAGER_TEST_POSTGRES_DSN`` set.

Each test uses its own table prefix and drops its tables afterwards, so tests
can share one database. Two stores on separate pools stand in for two processes.
"""

from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest

from feedback_manager.assessment import LearningDecision
from feedback_manager.experience import ExecutionExperience
from feedback_manager.storage.assessment import AssessmentConflictError, AssessmentStoreError
from feedback_manager.storage.experience import ExperienceConflictError, ExperienceStoreError
from feedback_manager.storage.payload import PayloadCodec, PayloadScope, PayloadStoreError
from feedback_manager.testing import (
    AssessmentRepositoryContract,
    ExperienceRepositoryContract,
    PayloadStoreContract,
)
from tests.learning.test_recovery import accepted_assessment
from tests.learning.test_worker import SCOPE as WORKER_SCOPE
from tests.learning.test_worker import submit, worker

DSN = os.environ.get("FEEDBACK_MANAGER_TEST_POSTGRES_DSN", "")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not DSN, reason="set FEEDBACK_MANAGER_TEST_POSTGRES_DSN"),
]

sql = pytest.importorskip("psycopg.sql", reason="needs feedback-manager[postgres]")
psycopg_pool = pytest.importorskip("psycopg_pool", reason="needs feedback-manager[postgres]")
postgres = pytest.importorskip("feedback_manager.storage.postgres")

SCOPE = PayloadScope.model_validate(WORKER_SCOPE.model_dump())


def prefix():
    return f"fm_t{uuid4().hex[:12]}_"


def run(scenario):
    """psycopg's async mode needs a selector loop, which is not the Windows default."""
    asyncio.run(scenario, loop_factory=asyncio.SelectorEventLoop)


class PostgresKit:
    def run_scenario(self, scenario):
        run(scenario)

    async def release(self, component):
        await component.drop_schema()
        await component.close()


class TestPostgresPayloadStore(PostgresKit, PayloadStoreContract):
    async def make_payload_store(self):
        return await postgres.PostgresPayloadStore.open(DSN, table_prefix=prefix())


class TestPostgresExperienceRepository(PostgresKit, ExperienceRepositoryContract):
    async def make_experience_repository(self):
        return await postgres.PostgresExperienceRepository.open(DSN, table_prefix=prefix())


class TestPostgresAssessmentRepository(PostgresKit, AssessmentRepositoryContract):
    async def make_assessment_repository(self):
        return await postgres.PostgresAssessmentRepository.open(DSN, table_prefix=prefix())


async def shared_pool():
    pool = psycopg_pool.AsyncConnectionPool(
        DSN, min_size=1, max_size=4, open=False, kwargs={"autocommit": True}
    )
    await pool.open(wait=True)
    return pool


class Reversing(PayloadCodec):
    async def encode(self, scope, data):
        return data[::-1]

    async def decode(self, scope, data):
        return data[::-1]


def test_injected_pool_is_shared_and_left_open():
    async def scenario():
        pool = await shared_pool()
        try:
            table = prefix()
            async with await postgres.PostgresPayloadStore.open(
                pool, table_prefix=table, codec=Reversing()
            ) as store:
                await store.health()
                written = await store.write(
                    SCOPE, b"data", content_type="text/plain", serialization_version="1"
                )
                # A second store on the same tables and pool sees the write.
                other = postgres.PostgresPayloadStore(pool, table_prefix=table, codec=Reversing())
                assert await other.read(SCOPE, written.payload_id) == b"data"
                await store.drop_schema()
            assert not pool.closed
        finally:
            await pool.close()

    run(scenario())


def test_owned_pool_is_closed_and_construction_is_validated():
    async def scenario():
        store = await postgres.PostgresExperienceRepository.open(DSN, table_prefix=prefix())
        pool = store._pool
        await store.drop_schema()
        await store.close()
        assert pool.closed
        with pytest.raises(TypeError, match="codec"):
            await postgres.PostgresPayloadStore.open(DSN, codec=object())
        pool = await shared_pool()
        try:
            with pytest.raises(ValueError, match="table_prefix"):
                postgres.PostgresPayloadStore(pool, table_prefix='x"; DROP TABLE y; --')
        finally:
            await pool.close()
        with pytest.raises(TypeError, match="AsyncConnectionPool"):
            postgres.PostgresPayloadStore(DSN)

    run(scenario())


def test_database_errors_become_store_errors_without_details():
    async def scenario():
        store = await postgres.PostgresPayloadStore.open(DSN, table_prefix=prefix())
        await store.drop_schema()
        with pytest.raises(PayloadStoreError, match=r"^PostgreSQL operation failed \(") as error:
            await store.metadata(SCOPE, uuid4())
        assert "fm_t" not in str(error.value)
        await store.close()

    run(scenario())


def test_compare_and_set_holds_across_independent_pools():
    async def scenario():
        table = prefix()
        first = await postgres.PostgresExperienceRepository.open(DSN, table_prefix=table)
        second = await postgres.PostgresExperienceRepository.open(DSN, table_prefix=table)
        try:
            created = await first.create(ExecutionExperience(scope=SCOPE, execution_id="run"))
            racing = await asyncio.gather(
                *(
                    store.update(
                        created.model_copy(
                            update={
                                "version": 2,
                                "updated_at": created.updated_at.replace(year=2100),
                            }
                        ),
                        expected_version=1,
                    )
                    for store in (first, second, first, second)
                ),
                return_exceptions=True,
            )
            winners = [result for result in racing if isinstance(result, ExecutionExperience)]
            assert len(winners) == 1
            assert all(
                isinstance(result, ExperienceConflictError)
                for result in racing
                if result is not winners[0]
            )
            history = await second.versions(SCOPE, created.experience_id)
            assert [item.version for item in history] == [1, 2]
            assert await second.delete(SCOPE, created.experience_id)
        finally:
            await first.drop_schema()
            await first.close()
            await second.close()

    run(scenario())


def test_concurrent_decisions_appear_once_in_commit_order():
    async def scenario():
        table = prefix()
        experiences = await postgres.PostgresExperienceRepository.open(DSN, table_prefix=table)
        stores = [
            await postgres.PostgresAssessmentRepository.open(DSN, table_prefix=table)
            for _ in range(2)
        ]
        try:
            manager, _, _ = worker()
            accepted = [
                await accepted_assessment(await submit(manager), experiences, stores[0])
                for _ in range(3)
            ]
            reviewed = [
                await stores[0].create(item.model_copy(update={"assessment_id": uuid4()}))
                for item in accepted
                for _ in range(2)
            ]
            decisions = [
                LearningDecision(
                    scope=SCOPE,
                    assessment_id=item.assessment_id,
                    assessment_version=1,
                    accepted=index % 2 == 0,
                    reviewer="reviewer",
                )
                for index, item in enumerate(reviewed)
            ]
            await asyncio.gather(
                *(stores[index % 2].decide(decision) for index, decision in enumerate(decisions))
            )
            page = await stores[1].decision_changes(limit=1000)
            positions = [int(change.cursor) for change in page.changes]
            assert positions == sorted(positions)
            assert len(page.changes) == len(accepted) + len(decisions)
            assert not page.has_more
            spare = await stores[0].create(
                accepted[0].model_copy(update={"assessment_id": uuid4()})
            )
            reused = decisions[0].model_copy(update={"assessment_id": spare.assessment_id})
            with pytest.raises(AssessmentConflictError, match="decision ID"):
                await stores[1].decide(reused)
        finally:
            await experiences.drop_schema()
            await stores[0].drop_schema()
            await experiences.close()
            for store in stores:
                await store.close()

    run(scenario())


def test_assessment_store_errors_are_translated():
    async def scenario():
        store = await postgres.PostgresAssessmentRepository.open(DSN, table_prefix=prefix())
        await store.drop_schema()
        with pytest.raises(AssessmentStoreError, match="PostgreSQL operation failed"):
            await store.decision_changes()
        await store.close()

    run(scenario())


def test_rows_that_do_not_match_their_keys_are_rejected():
    async def scenario():
        table = prefix()
        experiences = await postgres.PostgresExperienceRepository.open(DSN, table_prefix=table)
        assessments = await postgres.PostgresAssessmentRepository.open(DSN, table_prefix=table)
        try:
            manager, _, _ = worker()
            accepted = await accepted_assessment(await submit(manager), experiences, assessments)
            experience_id = accepted.experience_id
            with pytest.raises(TypeError, match="UUID"):
                await experiences.get(SCOPE, str(experience_id))
            async with experiences._pool.connection() as connection:
                for name, statement in (
                    ("experiences", "UPDATE {} SET version=9"),
                    ("assessments", "UPDATE {} SET version=9"),
                    ("assessment_decisions", "UPDATE {} SET scope='{{}}'"),
                ):
                    await connection.execute(
                        sql.SQL(statement).format(sql.Identifier(table + name))
                    )
            with pytest.raises(ExperienceStoreError, match="inconsistent"):
                await experiences.get(SCOPE, experience_id)
            with pytest.raises(AssessmentStoreError, match="inconsistent"):
                await assessments.get(SCOPE, accepted.assessment_id)
            with pytest.raises(AssessmentStoreError, match="inconsistent"):
                await assessments.decision_changes()
        finally:
            await experiences.drop_schema()
            await assessments.drop_schema()
            await experiences.close()
            await assessments.close()

    run(scenario())
