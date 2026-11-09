"""The `FrameworkAdapter` for LangChain and LangGraph."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Literal

from feedback_manager.integrations.base import FrameworkAdapter
from feedback_manager.integrations.frameworks.langchain.context import LangChainContextExtractor
from feedback_manager.integrations.frameworks.langchain.failures import (
    LangChainFailureClassifier,
)
from feedback_manager.integrations.frameworks.langchain.interrupt import (
    LangGraphHumanInTheLoopBridge,
)
from feedback_manager.integrations.frameworks.langchain.learning import LangChainContextInjector

if TYPE_CHECKING:
    from feedback_manager.api.manager import FeedbackManager
    from feedback_manager.integrations.base import LearnedArtifactRetriever


class LangChainAdapter(FrameworkAdapter):
    """Integrates LangChain runnables and LangGraph graphs as one framework family.

    Args:
        message_role: The role of learned-context messages; see
            `LangChainContextInjector`.
    """

    name: ClassVar[str] = "langchain"

    def __init__(self, *, message_role: Literal["system", "human"] = "system") -> None:
        self._message_role: Literal["system", "human"] = message_role

    def context_extractor(self) -> LangChainContextExtractor:
        """Return a `LangChainContextExtractor`."""
        return LangChainContextExtractor()

    def failure_classifier(self) -> LangChainFailureClassifier:
        """Return a `LangChainFailureClassifier`."""
        return LangChainFailureClassifier()

    def human_in_the_loop(self, manager: FeedbackManager) -> LangGraphHumanInTheLoopBridge:
        """Return a `LangGraphHumanInTheLoopBridge` recording with ``manager``."""
        return LangGraphHumanInTheLoopBridge(manager)

    def context_injector(self, retriever: LearnedArtifactRetriever) -> LangChainContextInjector:
        """Return a `LangChainContextInjector` retrieving through ``retriever``."""
        return LangChainContextInjector(retriever, role=self._message_role)


__all__ = ["LangChainAdapter"]
