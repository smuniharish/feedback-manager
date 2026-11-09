"""LangChain and LangGraph integration, installed with ``feedback-manager[langchain]``.

LangChain and LangGraph are treated as one framework family: one adapter
covers LangChain callbacks and tools and LangGraph graphs, interrupts, and
streaming. Everything here implements the contracts in
`feedback_manager.integrations.base`.
"""

try:
    import langchain_core  # noqa: F401
    import langgraph  # noqa: F401
except ImportError as exc:  # pragma: no cover - exercised in a subprocess test
    raise ImportError(
        "feedback_manager.integrations.frameworks.langchain needs LangChain and LangGraph; "
        'install them with: pip install "feedback-manager[langchain]"'
    ) from exc

from feedback_manager.integrations.frameworks.langchain.adapter import LangChainAdapter
from feedback_manager.integrations.frameworks.langchain.callbacks import FeedbackCallbackHandler
from feedback_manager.integrations.frameworks.langchain.context import (
    LangChainContextExtractor,
    execution_context_from_config,
    execution_context_from_snapshot,
)
from feedback_manager.integrations.frameworks.langchain.failures import (
    LangChainFailureClassifier,
    category_for_error,
)
from feedback_manager.integrations.frameworks.langchain.interrupt import (
    LangGraphHumanInTheLoopBridge,
)
from feedback_manager.integrations.frameworks.langchain.learning import LangChainContextInjector
from feedback_manager.integrations.frameworks.langchain.streaming import (
    INTERRUPT_KEY,
    extract_interrupts,
)
from feedback_manager.integrations.frameworks.langchain.tools import capture_tool_feedback

__all__ = [
    "INTERRUPT_KEY",
    "FeedbackCallbackHandler",
    "LangChainAdapter",
    "LangChainContextExtractor",
    "LangChainContextInjector",
    "LangChainFailureClassifier",
    "LangGraphHumanInTheLoopBridge",
    "capture_tool_feedback",
    "category_for_error",
    "execution_context_from_config",
    "execution_context_from_snapshot",
    "extract_interrupts",
]
