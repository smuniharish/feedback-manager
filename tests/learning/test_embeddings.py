import math

import pytest

from feedback_manager.embeddings import (
    CallableEmbeddingProvider,
    EmbeddingProvider,
    EmbeddingSpace,
    HashingEmbeddingProvider,
    ensure_compatible,
    l2_normalize,
    validate_embeddings,
)
from feedback_manager.learning import LearningValidationError
from feedback_manager.learning.errors import EmbeddingCompatibilityError

SPACE = EmbeddingSpace(provider="test", model="fixed", revision="1", dimensions=3)


def cosine(left, right) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


class TestHashingEmbeddingProvider:
    async def test_is_deterministic_normalized_and_sized(self) -> None:
        provider = HashingEmbeddingProvider(64)

        first, second = await provider.embed_documents(["Retry the search", "Retry the search"])

        assert first == second
        assert len(first) == 64
        assert math.isclose(math.fsum(value * value for value in first), 1.0)
        assert provider.space.normalized
        assert provider.space.dimensions == 64

    async def test_texts_sharing_words_are_closer(self) -> None:
        provider = HashingEmbeddingProvider()
        query = await provider.embed_query("validate search results")
        related, unrelated = await provider.embed_documents(
            [
                "Always validate search results before executing.",
                "Refunds above 500 EUR need a manager.",
            ]
        )

        assert cosine(query, related) > cosine(query, unrelated)

    async def test_casing_is_ignored(self) -> None:
        provider = HashingEmbeddingProvider(32)

        assert await provider.embed_query("Search") == await provider.embed_query("search")

    async def test_text_without_words_is_the_zero_vector(self) -> None:
        provider = HashingEmbeddingProvider(16)

        assert await provider.embed_query("!!! ...") == (0.0,) * 16

    async def test_revision_changes_the_space(self) -> None:
        assert (
            HashingEmbeddingProvider(revision="1").space
            != HashingEmbeddingProvider(revision="2").space
        )

    def test_rejects_tiny_dimensions(self) -> None:
        with pytest.raises(LearningValidationError, match="at least 16"):
            HashingEmbeddingProvider(8)

    async def test_rejects_invalid_documents(self) -> None:
        provider = HashingEmbeddingProvider()

        with pytest.raises(LearningValidationError, match="not a string"):
            await provider.embed_documents("abc")
        with pytest.raises(LearningValidationError, match="must be a string"):
            await provider.embed_documents([1])  # type: ignore[list-item]
        with pytest.raises(LearningValidationError, match="at most"):
            await provider.embed_documents(["x"] * 2049)

    async def test_is_always_healthy(self) -> None:
        await HashingEmbeddingProvider().health()
        assert isinstance(HashingEmbeddingProvider(), EmbeddingProvider)


class TestCallableEmbeddingProvider:
    async def test_wraps_a_synchronous_function_with_instructions(self) -> None:
        seen: list[list[str]] = []

        def embed(texts):
            seen.append(list(texts))
            return [[1.0, 0.0, 0.0] for _ in texts]

        space = SPACE.model_copy(
            update={"document_instruction": "doc: ", "query_instruction": "q: "}
        )
        provider = CallableEmbeddingProvider(space, embed)

        assert await provider.embed_documents(["a", "b"]) == ((1.0, 0.0, 0.0), (1.0, 0.0, 0.0))
        assert await provider.embed_query("c") == (1.0, 0.0, 0.0)
        assert seen == [["doc: a", "doc: b"], ["q: c"]]
        assert provider.space is space

    async def test_wraps_asynchronous_functions_and_a_separate_query_function(self) -> None:
        async def documents(texts):
            return [[0.0, 3.0, 4.0] for _ in texts]

        async def queries(texts):
            return [[0.0, 0.0, 2.0] for _ in texts]

        provider = CallableEmbeddingProvider(
            SPACE.model_copy(update={"normalized": True}), documents, embed_queries=queries
        )

        assert await provider.embed_documents(["a"]) == ((0.0, 0.6, 0.8),)
        assert await provider.embed_query("b") == (0.0, 0.0, 1.0)

    async def test_an_empty_batch_does_not_call_the_function(self) -> None:
        def fail(texts):
            raise AssertionError("called")

        assert await CallableEmbeddingProvider(SPACE, fail).embed_documents([]) == ()

    @pytest.mark.parametrize(
        ("output", "message"),
        [
            ([], "returned 0 vector"),
            ([[1.0, 2.0]], "2 dimension"),
            ([[1.0, math.nan, 0.0]], "non-finite"),
        ],
    )
    async def test_rejects_invalid_output(self, output, message) -> None:
        provider = CallableEmbeddingProvider(SPACE, lambda texts: output)

        with pytest.raises(LearningValidationError, match=message):
            await provider.embed_query("x")

    async def test_health_probes_the_function(self) -> None:
        def broken(texts):
            raise ConnectionError("down")

        await CallableEmbeddingProvider(SPACE, lambda texts: [[1.0, 0.0, 0.0]]).health()
        with pytest.raises(ConnectionError):
            await CallableEmbeddingProvider(SPACE, broken).health()

    def test_rejects_invalid_configuration(self) -> None:
        with pytest.raises(TypeError, match="EmbeddingSpace"):
            CallableEmbeddingProvider({"dimensions": 3}, lambda texts: [])  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="embed_documents"):
            CallableEmbeddingProvider(SPACE, "not callable")  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="embed_queries"):
            CallableEmbeddingProvider(SPACE, lambda texts: [], embed_queries=1)  # type: ignore[arg-type]


class TestHelpers:
    def test_identical_spaces_are_compatible(self) -> None:
        ensure_compatible(SPACE, SPACE.model_copy())

    def test_different_spaces_name_the_differences(self) -> None:
        other = SPACE.model_copy(update={"revision": "2", "dimensions": 4})

        with pytest.raises(EmbeddingCompatibilityError, match="dimensions, revision"):
            ensure_compatible(SPACE, other)

    def test_l2_normalize_keeps_the_zero_vector(self) -> None:
        assert l2_normalize([0, 0]) == (0.0, 0.0)
        assert l2_normalize([3, 4]) == (0.6, 0.8)

    def test_validate_embeddings_returns_tuples(self) -> None:
        assert validate_embeddings(SPACE, [[1, 2, 3]], expected=1) == ((1.0, 2.0, 3.0),)
