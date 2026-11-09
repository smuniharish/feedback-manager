"""Replaceable vector-storage contracts and the built-in in-memory adapter.

The LanceDB adapter lives in `feedback_manager.storage.vector.lancedb` and is
imported explicitly because it needs the optional ``lancedb`` extra.
"""

from feedback_manager.storage.vector.base import (
    VectorCapabilities,
    VectorMatch,
    VectorRecord,
    VectorStore,
)
from feedback_manager.storage.vector.memory import InMemoryVectorStore

__all__ = [
    "InMemoryVectorStore",
    "VectorCapabilities",
    "VectorMatch",
    "VectorRecord",
    "VectorStore",
]
