"""Real LangChain runnables that fail on demand, for integration tests."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain_core.documents import Document
from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.llms import LLM
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.retrievers import BaseRetriever

if TYPE_CHECKING:
    from langchain_core.callbacks import CallbackManagerForLLMRun, CallbackManagerForRetrieverRun


class FakeChatModel(BaseChatModel):
    """A chat model that answers ``reply`` or raises ``error``."""

    error: Any = None
    reply: str = "ok"

    @property
    def _llm_type(self) -> str:
        return "fake-chat"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        if self.error is not None:
            raise self.error
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self.reply))])


class FakeLLM(LLM):
    """A completion model that answers ``"ok"`` or raises ``error``."""

    error: Any = None

    @property
    def _llm_type(self) -> str:
        return "fake-llm"

    def _call(
        self,
        prompt: str,
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> str:
        if self.error is not None:
            raise self.error
        return "ok"


class FakeRetriever(BaseRetriever):
    """A retriever that echoes the query or raises ``error``."""

    error: Any = None

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> list[Document]:
        if self.error is not None:
            raise self.error
        return [Document(page_content=query)]
