"""feedback-manager: feedback infrastructure for LangChain and LangGraph applications.

Capture, correlate, store, route, and resolve feedback as a first-class domain
concern. LangChain and LangGraph keep owning execution, state, checkpoints,
interrupts, and streaming; feedback-manager owns the feedback about them.

```python
from feedback_manager import FeedbackManager, FeedbackTarget, FeedbackTargetType

manager = FeedbackManager()
feedback = await manager.submit(
    source="human",
    category="correction",
    target=FeedbackTarget(type=FeedbackTargetType.GENERATION, id="gen-1"),
    payload={"corrected_text": "Canberra is the capital of Australia."},
)
```
"""

from importlib.metadata import PackageNotFoundError, version

from feedback_manager.api.manager import FeedbackManager
from feedback_manager.api.stream import FeedbackStream
from feedback_manager.api.subscription import Subscription
from feedback_manager.contracts.store import FeedbackQuery
from feedback_manager.core.categories import FeedbackCategory
from feedback_manager.core.context import ExecutionContext
from feedback_manager.core.events import FeedbackEvent
from feedback_manager.core.lifecycle import validate_transition
from feedback_manager.core.provenance import FeedbackProvenanceReference
from feedback_manager.core.sources import FeedbackSource
from feedback_manager.core.status import FeedbackStatus
from feedback_manager.core.targets import FeedbackTarget, FeedbackTargetType
from feedback_manager.errors import (
    FeedbackConfigurationError,
    FeedbackConflictError,
    FeedbackCorrelationError,
    FeedbackHandlerError,
    FeedbackLifecycleError,
    FeedbackManagerError,
    FeedbackNotFoundError,
    FeedbackRoutingError,
    FeedbackStoreError,
    FeedbackSubscriberError,
    FeedbackValidationError,
)

try:
    __version__ = version("feedback-manager")
except PackageNotFoundError:  # pragma: no cover - only when imported from a source tree
    __version__ = "0.0.0"

__all__ = [
    "ExecutionContext",
    "FeedbackCategory",
    "FeedbackConfigurationError",
    "FeedbackConflictError",
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
    "FeedbackSource",
    "FeedbackStatus",
    "FeedbackStoreError",
    "FeedbackStream",
    "FeedbackSubscriberError",
    "FeedbackTarget",
    "FeedbackTargetType",
    "FeedbackValidationError",
    "Subscription",
    "__version__",
    "validate_transition",
]
