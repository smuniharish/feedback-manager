"""Framework-neutral integration contracts and helpers.

Importing this package never imports an agent framework. Framework adapters
live in `feedback_manager.integrations.frameworks`; provenance providers live
in `feedback_manager.integrations.provenance`.
"""

from feedback_manager.integrations.base import (
    LEARNED_CONTEXT_HEADER,
    ExecutionContextExtractor,
    FailureClassifier,
    FrameworkAdapter,
    HumanInTheLoopBridge,
    LearnedArtifactRetriever,
    LearnedContextInjector,
    TextContextInjector,
    render_learned_context,
)
from feedback_manager.integrations.tools import capture_tool_feedback

__all__ = [
    "LEARNED_CONTEXT_HEADER",
    "ExecutionContextExtractor",
    "FailureClassifier",
    "FrameworkAdapter",
    "HumanInTheLoopBridge",
    "LearnedArtifactRetriever",
    "LearnedContextInjector",
    "TextContextInjector",
    "capture_tool_feedback",
    "render_learned_context",
]
