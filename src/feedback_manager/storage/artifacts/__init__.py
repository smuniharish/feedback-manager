"""Authoritative learned-artifact storage contracts."""

from feedback_manager.storage.artifacts.base import (
    ArtifactPage,
    ArtifactQuery,
    ArtifactRepository,
    ProjectionIntent,
)
from feedback_manager.storage.artifacts.memory import InMemoryArtifactRepository

__all__ = [
    "ArtifactPage",
    "ArtifactQuery",
    "ArtifactRepository",
    "InMemoryArtifactRepository",
    "ProjectionIntent",
]
