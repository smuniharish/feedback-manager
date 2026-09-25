"""LangChain integration adapters (thin, reuses LangChain's own callbacks)."""

from feedback_manager.integrations.langchain.callbacks import FeedbackCallbackHandler
from feedback_manager.integrations.langchain.tools import capture_tool_feedback

__all__ = ["FeedbackCallbackHandler", "capture_tool_feedback"]
