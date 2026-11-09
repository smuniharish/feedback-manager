"""Every bundled component, and the SQLite examples, certified with the public kits."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from feedback_manager.embeddings import (
    CallableEmbeddingProvider,
    EmbeddingSpace,
    HashingEmbeddingProvider,
)
from feedback_manager.learning import InMemoryLearningJobStore, SQLiteLearningJobStore
from feedback_manager.storage import InMemoryFeedbackStore
from feedback_manager.storage.artifacts import InMemoryArtifactRepository
from feedback_manager.storage.graph import GraphCapabilities, InMemoryGraphStore
from feedback_manager.storage.vector import InMemoryVectorStore
from feedback_manager.testing import (
    ArtifactRepositoryContract,
    EmbeddingProviderContract,
    FeedbackStoreContract,
    GraphStoreContract,
    LearningJobStoreContract,
    VectorStoreContract,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path
    from uuid import UUID

    from feedback_manager.contracts import FeedbackStore
    from feedback_manager.core.events import FeedbackEvent
    from feedback_manager.embeddings import EmbeddingProvider
    from feedback_manager.learning import LearningJobStore
    from feedback_manager.storage.artifacts import ArtifactRepository
    from feedback_manager.storage.graph import GraphStore
    from feedback_manager.storage.vector import VectorStore


class UsesTmpPath:
    directory: Path

    @pytest.fixture(autouse=True)
    def _directory(self, tmp_path: Path) -> None:
        self.directory = tmp_path


class TestInMemoryFeedbackStore(FeedbackStoreContract):
    async def make_store(self) -> FeedbackStore:
        return InMemoryFeedbackStore()


class TestSQLiteFeedbackStoreExample(UsesTmpPath, FeedbackStoreContract):
    async def make_store(self) -> FeedbackStore:
        from examples.sqlite_feedback_store import SQLiteFeedbackStore

        return SQLiteFeedbackStore(self.directory / "feedback.db")


class TestInMemoryArtifactRepository(ArtifactRepositoryContract):
    async def make_repository(self) -> ArtifactRepository:
        return InMemoryArtifactRepository()


class TestSQLiteArtifactRepositoryExample(UsesTmpPath, ArtifactRepositoryContract):
    async def make_repository(self) -> ArtifactRepository:
        from examples.sqlite_artifact_repository import SQLiteArtifactRepository

        return SQLiteArtifactRepository(self.directory / "artifacts.db")


class TestInMemoryVectorStore(VectorStoreContract):
    async def make_vector_store(self) -> VectorStore:
        return InMemoryVectorStore()


class TestLanceDBVectorStore(UsesTmpPath, VectorStoreContract):
    async def make_vector_store(self) -> VectorStore:
        pytest.importorskip("lancedb")
        from feedback_manager.storage.vector.lancedb import LanceDBVectorStore

        return LanceDBVectorStore(str(self.directory / "vectors"))


class TestInMemoryGraphStore(GraphStoreContract):
    async def make_graph_store(self) -> GraphStore:
        return InMemoryGraphStore()


class TestLadybugGraphStore(UsesTmpPath, GraphStoreContract):
    async def make_graph_store(self) -> GraphStore:
        pytest.importorskip("ladybug")
        from feedback_manager.storage.graph.ladybug import LadybugGraphStore

        return LadybugGraphStore(self.directory / "graph.lbug")


class TestInMemoryLearningJobStore(LearningJobStoreContract):
    async def make_job_store(self) -> LearningJobStore:
        return InMemoryLearningJobStore()


class TestSQLiteLearningJobStore(UsesTmpPath, LearningJobStoreContract):
    async def make_job_store(self) -> LearningJobStore:
        return SQLiteLearningJobStore(self.directory / "jobs.db")


class TestHashingEmbeddingProvider(EmbeddingProviderContract):
    async def make_provider(self) -> EmbeddingProvider:
        return HashingEmbeddingProvider(dimensions=32)


class TestCallableEmbeddingProvider(EmbeddingProviderContract):
    async def make_provider(self) -> EmbeddingProvider:
        def embed(documents: Sequence[str]) -> list[list[float]]:
            return [[float(len(document)), 1.0] for document in documents]

        space = EmbeddingSpace(
            provider="contract", model="length", revision="1", dimensions=2, normalized=True
        )
        return CallableEmbeddingProvider(space, embed)


class TestUnnormalizedEmbeddingProvider(EmbeddingProviderContract):
    async def make_provider(self) -> EmbeddingProvider:
        space = EmbeddingSpace(provider="contract", model="raw", revision="1", dimensions=2)
        return CallableEmbeddingProvider(space, lambda docs: [[3.0, 4.0] for _ in docs])


class UnpagedGraphStore(InMemoryGraphStore):
    @property
    def capabilities(self) -> GraphCapabilities:
        return super().capabilities.model_copy(update={"pagination": False})


class TestUnpagedGraphStore(GraphStoreContract):
    async def make_graph_store(self) -> GraphStore:
        return UnpagedGraphStore()


class Closable:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class AsyncClosable:
    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


async def test_release_calls_sync_or_async_close_when_present() -> None:
    class Kit(EmbeddingProviderContract):
        async def make_provider(self) -> EmbeddingProvider:
            raise NotImplementedError

    kit = Kit()
    closable, async_closable = Closable(), AsyncClosable()
    await kit.release(closable)  # type: ignore[arg-type]
    await kit.release(async_closable)  # type: ignore[arg-type]
    await kit.release(object())  # type: ignore[arg-type]
    assert closable.closed
    assert async_closable.closed


def test_a_broken_component_fails_with_a_descriptive_message() -> None:
    class Forgetful(InMemoryFeedbackStore):
        async def get(self, feedback_id: UUID) -> FeedbackEvent | None:
            return None

    class Kit(FeedbackStoreContract):
        async def make_store(self) -> FeedbackStore:
            return Forgetful()

    with pytest.raises(AssertionError, match="get returned None"):
        Kit().test_create_and_get_round_trip()


def test_a_component_that_never_raises_fails_the_contract() -> None:
    class Lenient(InMemoryFeedbackStore):
        async def create(self, feedback: FeedbackEvent) -> FeedbackEvent:
            return feedback

    class Kit(FeedbackStoreContract):
        async def make_store(self) -> FeedbackStore:
            return Lenient()

    with pytest.raises(AssertionError, match="duplicate feedback_id"):
        Kit().test_duplicate_feedback_ids_are_rejected()
