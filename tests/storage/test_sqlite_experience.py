import asyncio
import sqlite3
import threading
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from feedback_manager.experience import ExecutionExperience
from feedback_manager.learning.errors import LearningValidationError
from feedback_manager.storage.experience import (
    ExperienceConflictError,
    ExperienceStoreError,
    SQLiteExperienceRepository,
)
from feedback_manager.storage.payload import PayloadScope

SCOPE = PayloadScope(tenant_id="tenant", namespace="app", agent_id="agent")
NOW = datetime(2026, 1, 1, tzinfo=UTC)


async def test_restart_and_competing_connections_preserve_history(tmp_path: Path) -> None:
    path = tmp_path / "experiences.db"
    first = await SQLiteExperienceRepository.open(path)
    other = await SQLiteExperienceRepository.open(path)
    original = ExecutionExperience(scope=SCOPE, execution_id="run", created_at=NOW, updated_at=NOW)
    updated = original.model_copy(
        update={"version": 2, "updated_at": NOW + timedelta(seconds=1), "status": "completed"}
    )
    try:
        await first.create(original)
        results = await asyncio.gather(
            first.update(updated, expected_version=1),
            other.update(updated, expected_version=1),
            return_exceptions=True,
        )
        assert sum(isinstance(result, ExperienceConflictError) for result in results) == 1
        assert sum(result == updated for result in results) == 1
    finally:
        await first.close()
        await other.close()

    restarted = await SQLiteExperienceRepository.open(path)
    try:
        assert restarted.durable
        assert await restarted.get(SCOPE, original.experience_id) == updated
        assert await restarted.versions(SCOPE, original.experience_id) == (original, updated)
        assert await restarted.create(original) == updated
        assert await restarted.versions(SCOPE, original.experience_id, after_version=10**100) == ()
    finally:
        await restarted.close()
        await restarted.close()
    with pytest.raises(ExperienceStoreError, match="closed"):
        await restarted.health()


async def test_failed_version_insert_rolls_back_current_record(tmp_path: Path) -> None:
    path = tmp_path / "experiences.db"
    repository = await SQLiteExperienceRepository.open(path)
    original = ExecutionExperience(scope=SCOPE, execution_id="run", created_at=NOW, updated_at=NOW)
    updated = original.model_copy(update={"version": 2, "updated_at": NOW + timedelta(seconds=1)})
    try:
        await repository.create(original)
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute(
                "CREATE TRIGGER fail_version BEFORE INSERT ON experience_versions "
                "WHEN NEW.version=2 BEGIN SELECT RAISE(ABORT, 'injected'); END"
            )
        with pytest.raises(sqlite3.IntegrityError, match="injected"):
            await repository.update(updated, expected_version=1)
        assert await repository.get(SCOPE, original.experience_id) == original
        assert await repository.versions(SCOPE, original.experience_id) == (original,)
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("DROP TRIGGER fail_version")
        assert await repository.update(updated, expected_version=1) == updated
    finally:
        await repository.close()


async def test_corrupt_rows_cannot_return_foreign_scoped_experiences(tmp_path: Path) -> None:
    path = tmp_path / "experiences.db"
    repository = await SQLiteExperienceRepository.open(path)
    original = ExecutionExperience(scope=SCOPE, execution_id="run")
    foreign = original.model_copy(
        update={"scope": PayloadScope(tenant_id="foreign", namespace="app", agent_id="agent")}
    )
    try:
        await repository.create(original)
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute(
                "UPDATE experience_records SET document=?", (foreign.model_dump_json(),)
            )
        with pytest.raises(ExperienceStoreError, match="identity"):
            await repository.get(SCOPE, original.experience_id)
        with pytest.raises(ExperienceStoreError, match="identity"):
            await repository.query(SCOPE)
        with pytest.raises(ExperienceStoreError, match="identity"):
            await repository.versions(SCOPE, original.experience_id)
    finally:
        await repository.close()


async def test_cancelled_open_closes_constructed_connection(tmp_path: Path) -> None:
    entered, release = threading.Event(), threading.Event()
    closing, release_close = asyncio.Event(), asyncio.Event()
    created: list[SQLiteExperienceRepository] = []

    class DelayedRepository(SQLiteExperienceRepository):
        def __init__(self, path: str | Path) -> None:
            entered.set()
            if not release.wait(timeout=5):
                raise RuntimeError("open did not resume")
            super().__init__(path)
            created.append(self)

        async def close(self) -> None:
            closing.set()
            await release_close.wait()
            await super().close()

    pending = asyncio.create_task(DelayedRepository.open(tmp_path / "experiences.db"))
    try:
        assert await asyncio.wait_for(asyncio.to_thread(entered.wait), timeout=2)
        pending.cancel()
        await asyncio.sleep(0)
        pending.cancel()
        await asyncio.sleep(0)
    finally:
        release.set()
    try:
        await asyncio.wait_for(closing.wait(), timeout=2)
        pending.cancel()
        await asyncio.sleep(0)
    finally:
        release_close.set()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert len(created) == 1
    with pytest.raises(ExperienceStoreError, match="closed"):
        await created[0].health()


@pytest.mark.parametrize("filename", ["", ":memory:", "file:archive?mode=memory"])
async def test_durable_archive_rejects_temporary_database_paths(filename: str) -> None:
    with pytest.raises(LearningValidationError, match="local database file"):
        await SQLiteExperienceRepository.open(filename)
