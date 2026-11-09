"""Replaceable graph-storage contracts."""

from feedback_manager.storage.graph.base import (
    GraphCapabilities,
    GraphDirection,
    GraphNode,
    GraphPage,
    GraphRelationship,
    GraphStore,
)
from feedback_manager.storage.graph.memory import InMemoryGraphStore

__all__ = [
    "GraphCapabilities",
    "GraphDirection",
    "GraphNode",
    "GraphPage",
    "GraphRelationship",
    "GraphStore",
    "InMemoryGraphStore",
]
