"""Embedding providers and embedding-space metadata."""

from feedback_manager.embeddings.base import EmbeddingProvider
from feedback_manager.embeddings.models import EmbeddingSpace
from feedback_manager.embeddings.providers import (
    CallableEmbeddingProvider,
    HashingEmbeddingProvider,
    ensure_compatible,
    l2_normalize,
    validate_embeddings,
)

__all__ = [
    "CallableEmbeddingProvider",
    "EmbeddingProvider",
    "EmbeddingSpace",
    "HashingEmbeddingProvider",
    "ensure_compatible",
    "l2_normalize",
    "validate_embeddings",
]
