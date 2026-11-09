"""Rendering learned artifacts as LangChain messages."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from feedback_manager.integrations.base import LearnedContextInjector, render_learned_context

if TYPE_CHECKING:
    from feedback_manager.integrations.base import LearnedArtifactRetriever
    from feedback_manager.retrieval.models import RetrievalResult


class LangChainContextInjector(LearnedContextInjector[BaseMessage]):
    """Renders learned context as a LangChain message to prepend to a model call.

    ```python
    injector = LangChainContextInjector(engine)
    message = await injector.build(RetrievalRequest(scope=scope, task=question))
    messages = [message, *messages] if message is not None else messages
    ```

    The message content is `render_learned_context`: reviewed artifact titles
    and content in a delimited block labelled as reference data. Learned
    content derives from feedback, so keep it separate from your own
    instructions.

    Args:
        retriever: Retrieves the artifacts, for example a `LearningEngine`.
        role: ``"system"`` (default) renders a ``SystemMessage``; ``"human"``
            renders a ``HumanMessage``, for models or policies that keep the
            system prompt for operator instructions only.
    """

    def __init__(
        self,
        retriever: LearnedArtifactRetriever,
        *,
        role: Literal["system", "human"] = "system",
    ) -> None:
        super().__init__(retriever)
        if role not in ("system", "human"):
            raise ValueError(f"role must be 'system' or 'human', not {role!r}")
        self._role = role

    def render(self, result: RetrievalResult) -> BaseMessage:
        """Return the learned context as a message with ``learned_context`` metadata."""
        content = render_learned_context(result)
        artifact_ids = [str(artifact.artifact_id) for artifact in result.artifacts]
        kwargs = {"learned_artifact_ids": artifact_ids}
        if self._role == "system":
            return SystemMessage(content=content, additional_kwargs=kwargs)
        return HumanMessage(content=content, additional_kwargs=kwargs)


__all__ = ["LangChainContextInjector"]
