"""Errors raised by the learning and retrieval components."""

from feedback_manager.errors import FeedbackManagerError


class LearningError(FeedbackManagerError):
    """Base class for learning failures."""


class LearningValidationError(LearningError, ValueError):
    """A learning input or artifact is invalid."""


class LearningScopeError(LearningValidationError):
    """A trustworthy tenant, namespace, or agent scope is missing."""


class ArtifactNotFoundError(LearningError, LookupError):
    """An artifact is not present in the requested scope."""


class ArtifactConflictError(LearningError):
    """An artifact changed concurrently or violates its lifecycle."""


class EmbeddingCompatibilityError(LearningValidationError):
    """Embeddings do not belong to the same embedding space."""


class UnsupportedCapabilityError(LearningError):
    """A configured provider does not implement a requested capability."""


__all__ = [
    "ArtifactConflictError",
    "ArtifactNotFoundError",
    "EmbeddingCompatibilityError",
    "LearningError",
    "LearningScopeError",
    "LearningValidationError",
    "UnsupportedCapabilityError",
]
