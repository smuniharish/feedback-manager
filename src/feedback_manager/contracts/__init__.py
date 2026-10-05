"""The extension contracts: implement one to replace a default collaborator.

Stateful extension points (`FeedbackStore`, `FeedbackRouter`,
`FeedbackHandler`, and the policies) are abstract base classes. Single-method
callables (`FeedbackCorrelator`, `FeedbackSubscriber`) are protocols, so plain
functions and objects satisfy them without subclassing.
"""

from feedback_manager.contracts.correlator import FeedbackCorrelator
from feedback_manager.contracts.handler import FeedbackHandler, FeedbackHandlerResult
from feedback_manager.contracts.policy import FeedbackLifecyclePolicy, FeedbackRedactionPolicy
from feedback_manager.contracts.router import FeedbackRouter
from feedback_manager.contracts.store import FeedbackQuery, FeedbackStore
from feedback_manager.contracts.subscriber import FeedbackSubscriber

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
]
