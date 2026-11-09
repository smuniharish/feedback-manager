"""The extension contracts: implement one to replace a default collaborator.

Every extension point is an abstract base class. `FeedbackManager.subscribe`
also accepts a plain ``async def`` function and wraps it in `FunctionSubscriber`.
"""

from feedback_manager.contracts.correlator import FeedbackCorrelator
from feedback_manager.contracts.handler import FeedbackHandler, FeedbackHandlerResult
from feedback_manager.contracts.policy import FeedbackLifecyclePolicy, FeedbackRedactionPolicy
from feedback_manager.contracts.provenance import ProvenanceProvider
from feedback_manager.contracts.router import FeedbackRouter
from feedback_manager.contracts.store import FeedbackQuery, FeedbackStore
from feedback_manager.contracts.subscriber import FeedbackSubscriber, FunctionSubscriber

__all__ = [
    "FeedbackCorrelator",
    "FeedbackHandler",
    "FeedbackHandlerResult",
    "FeedbackLifecyclePolicy",
    "FeedbackQuery",
    "FeedbackRedactionPolicy",
    "FeedbackRouter",
    "FeedbackStore",
    "FeedbackSubscriber",
    "FunctionSubscriber",
    "ProvenanceProvider",
]
