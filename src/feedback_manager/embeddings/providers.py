"""Dependency-free embedding providers and embedding validation.

`HashingEmbeddingProvider` is a deterministic, offline lexical embedder for
tests, air-gapped deployments, and as a baseline. `CallableEmbeddingProvider`
adapts any embedding function, such as an OpenAI, Ollama, or
sentence-transformers client, without feedback-manager depending on it.
"""

from __future__ import annotations

import hashlib
import inspect
import math
import re
from typing import TYPE_CHECKING

from feedback_manager.embeddings.base import EmbeddingProvider
from feedback_manager.embeddings.models import EmbeddingSpace
from feedback_manager.learning.errors import EmbeddingCompatibilityError, LearningValidationError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    type EmbedFunction = Callable[
        [Sequence[str]], Sequence[Sequence[float]] | Awaitable[Sequence[Sequence[float]]]
    ]

_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
_MAX_DOCUMENTS = 2048


def ensure_compatible(expected: EmbeddingSpace, actual: EmbeddingSpace) -> None:
    """Raise unless two embedding spaces are identical.

    Vectors from different models, revisions, dimensions, or instructions are
    not comparable, so they must never be searched against each other.

    Raises:
        EmbeddingCompatibilityError: If the spaces differ.
    """
    if expected != actual:
        differing = sorted(
            name
            for name in EmbeddingSpace.model_fields
            if getattr(expected, name) != getattr(actual, name)
        )
        raise EmbeddingCompatibilityError(
            f"embedding spaces differ in {', '.join(differing)}: "
            f"{expected.space_id[:12]} != {actual.space_id[:12]}"
        )


def l2_normalize(vector: Sequence[float]) -> tuple[float, ...]:
    """Return ``vector`` scaled to unit length; a zero vector is returned unchanged."""
    norm = math.sqrt(math.fsum(value * value for value in vector))
    if norm == 0.0:
        return tuple(float(value) for value in vector)
    return tuple(float(value) / norm for value in vector)


def validate_embeddings(
    space: EmbeddingSpace, vectors: Sequence[Sequence[float]], *, expected: int
) -> tuple[tuple[float, ...], ...]:
    """Check provider output against its space and return it as immutable tuples.

    Vectors are L2-normalized when the space declares ``normalized=True``.

    Raises:
        LearningValidationError: If the number of vectors, a dimension count,
            or a value is invalid.
    """
    if len(vectors) != expected:
        raise LearningValidationError(
            f"embedding provider returned {len(vectors)} vector(s) for {expected} input(s)"
        )
    checked: list[tuple[float, ...]] = []
    for index, vector in enumerate(vectors):
        if len(vector) != space.dimensions:
            raise LearningValidationError(
                f"embedding {index} has {len(vector)} dimension(s), expected {space.dimensions}"
            )
        values = tuple(float(value) for value in vector)
        if not all(math.isfinite(value) for value in values):
            raise LearningValidationError(f"embedding {index} contains a non-finite value")
        checked.append(l2_normalize(values) if space.normalized else values)
    return tuple(checked)


def _check_documents(documents: Sequence[str]) -> None:
    if isinstance(documents, str):
        raise LearningValidationError("documents must be a sequence of strings, not a string")
    if len(documents) > _MAX_DOCUMENTS:
        raise LearningValidationError(f"at most {_MAX_DOCUMENTS} documents can be embedded at once")
    for document in documents:
        if not isinstance(document, str):
            raise LearningValidationError("every document must be a string")


class HashingEmbeddingProvider(EmbeddingProvider):
    """A deterministic lexical embedder using signed feature hashing.

    Lower-cased word unigrams and bigrams are hashed with BLAKE2b into
    ``dimensions`` buckets, and the vector is L2-normalized. Texts sharing
    words have a high cosine similarity. It needs no model download or
    network access, so results are reproducible across processes and
    platforms; it does not capture synonyms the way neural models do.
    """

    def __init__(self, dimensions: int = 384, *, revision: str = "1") -> None:
        """Create a provider.

        Args:
            dimensions: The vector size; at least 16.
            revision: Change it to force re-indexing into a new space.
        """
        if dimensions < 16:
            raise LearningValidationError("dimensions must be at least 16")
        self._space = EmbeddingSpace(
            provider="feedback-manager",
            model="feature-hashing-blake2b",
            revision=revision,
            dimensions=dimensions,
            normalized=True,
        )

    @property
    def space(self) -> EmbeddingSpace:
        """The provider's embedding space."""
        return self._space

    def _embed(self, text: str) -> tuple[float, ...]:
        vector = [0.0] * self._space.dimensions
        tokens = [token.casefold() for token in _TOKEN.findall(text)]
        features = tokens + [f"{tokens[i]} {tokens[i + 1]}" for i in range(len(tokens) - 1)]
        for feature in features:
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "big") % self._space.dimensions
            vector[bucket] += 1.0 if digest[4] & 1 else -1.0
        return l2_normalize(vector)

    async def embed_documents(self, documents: Sequence[str]) -> Sequence[Sequence[float]]:
        """Embed documents in input order."""
        _check_documents(documents)
        return tuple(self._embed(document) for document in documents)

    async def embed_query(self, query: str) -> Sequence[float]:
        """Embed a query; queries and documents share one space."""
        _check_documents((query,))
        return self._embed(query)

    async def health(self) -> None:
        """Always healthy: the provider has no external dependency."""


class CallableEmbeddingProvider(EmbeddingProvider):
    """Adapts any synchronous or asynchronous embedding function.

    ```python
    from openai import AsyncOpenAI

    client = AsyncOpenAI()


    async def embed(texts):
        response = await client.embeddings.create(model="text-embedding-3-small", input=list(texts))
        return [item.embedding for item in response.data]


    provider = CallableEmbeddingProvider(
        EmbeddingSpace(
            provider="openai",
            model="text-embedding-3-small",
            revision="1",
            dimensions=1536,
            normalized=True,
        ),
        embed,
    )
    ```

    Output is validated against the space: one vector per input, the declared
    number of dimensions, and finite values. Instructions in the space are
    prefixed to documents and queries.
    """

    def __init__(
        self,
        space: EmbeddingSpace,
        embed_documents: EmbedFunction,
        *,
        embed_queries: EmbedFunction | None = None,
    ) -> None:
        """Create a provider.

        Args:
            space: The space the function embeds into.
            embed_documents: Embeds a batch of documents.
            embed_queries: Embeds a batch of queries; defaults to ``embed_documents``.
        """
        if not isinstance(space, EmbeddingSpace):
            raise TypeError(f"space must be an EmbeddingSpace, not {type(space).__name__}")
        if not callable(embed_documents):
            raise TypeError("embed_documents must be callable")
        if embed_queries is not None and not callable(embed_queries):
            raise TypeError("embed_queries must be callable")
        self._space = space
        self._documents = embed_documents
        self._queries = embed_queries or embed_documents

    @property
    def space(self) -> EmbeddingSpace:
        """The provider's embedding space."""
        return self._space

    @staticmethod
    async def _call(function: EmbedFunction, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        result = function(texts)
        if inspect.isawaitable(result):
            result = await result
        return result

    async def embed_documents(self, documents: Sequence[str]) -> Sequence[Sequence[float]]:
        """Embed documents in input order."""
        _check_documents(documents)
        if not documents:
            return ()
        texts = [f"{self._space.document_instruction}{document}" for document in documents]
        vectors = await self._call(self._documents, texts)
        return validate_embeddings(self._space, vectors, expected=len(texts))

    async def embed_query(self, query: str) -> Sequence[float]:
        """Embed a query with the space's query instruction."""
        _check_documents((query,))
        vectors = await self._call(self._queries, [f"{self._space.query_instruction}{query}"])
        return validate_embeddings(self._space, vectors, expected=1)[0]

    async def health(self) -> None:
        """Embed a short probe text, raising if the function fails or misbehaves."""
        await self.embed_query("health check")


__all__ = [
    "CallableEmbeddingProvider",
    "HashingEmbeddingProvider",
    "ensure_compatible",
    "l2_normalize",
    "validate_embeddings",
]
