"""FeedbackManager: feedback infrastructure for LangChain/LangGraph applications.

FeedbackManager captures, correlates, persists, routes, and manages the
lifecycle of feedback generated during or around agent execution. It is a
library, not a runtime: LangGraph/LangChain continue to own execution,
state, checkpoints, interrupts, and streaming.

The public surface is intentionally small. Most applications only need::

    from feedback_manager import FeedbackManager, FeedbackSource, FeedbackCategory, FeedbackTarget, FeedbackTargetType

    manager = FeedbackManager()
    event = await manager.submit(
        source=FeedbackSource.HUMAN,
        category=FeedbackCategory.CORRECTION,
        target=FeedbackTarget(type=FeedbackTargetType.GENERATION, id="gen-1"),
        payload={"corrected_text": "..."},
    )

See ``docs/`` for the full architecture, extension points, and framework
integrations (``feedback_manager.integrations``).
"""

from feedback_manager.api.manager import FeedbackManager
from feedback_manager.api.queries import FeedbackQuery
from feedback_manager.api.subscription import Subscription
from feedback_manager.core.categories import FeedbackCategory
from feedback_manager.core.context import CorrelationContext, ExecutionContext
from feedback_manager.core.events import FeedbackEvent
from feedback_manager.core.provenance import FeedbackProvenanceReference
from feedback_manager.core.sources import FeedbackSource
from feedback_manager.core.status import FeedbackStatus
from feedback_manager.core.targets import FeedbackTarget, FeedbackTargetType
from feedback_manager.errors.exceptions import (
    FeedbackConfigurationError,
    FeedbackCorrelationError,
    FeedbackHandlerError,
    FeedbackLifecycleError,
    FeedbackManagerError,
    FeedbackNotFoundError,
    FeedbackRoutingError,
    FeedbackSerializationError,
    FeedbackStoreError,
    FeedbackValidationError,
)

__version__ = "0.1.0"

__all__ = [
    "CorrelationContext",
    "ExecutionContext",
    "FeedbackCategory",
    "FeedbackConfigurationError",
    "FeedbackCorrelationError",
    "FeedbackEvent",
    "FeedbackHandlerError",
    "FeedbackLifecycleError",
    "FeedbackManager",
    "FeedbackManagerError",
    "FeedbackNotFoundError",
    "FeedbackProvenanceReference",
    "FeedbackQuery",
    "FeedbackRoutingError",
    "FeedbackSerializationError",
    "FeedbackSource",
    "FeedbackStatus",
    "FeedbackStoreError",
    "FeedbackTarget",
    "FeedbackTargetType",
    "FeedbackValidationError",
    "Subscription",
    "__version__",
]
