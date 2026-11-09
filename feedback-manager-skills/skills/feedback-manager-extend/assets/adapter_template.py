"""A FrameworkAdapter for an agent framework, to copy and adapt.

`RunInfo`, `Pause`, and `Resume` stand in for your framework's own types: its
run or config object, the object it pauses on, and the value it resumes with.
Delete them, import the framework's types instead, and keep each method's
behavior. Test the result with `test_adapter.py`.

The adapter only translates. The framework keeps owning execution, pauses,
and callbacks; never change its inputs, outputs, or control flow.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from feedback_manager import ExecutionContext
from feedback_manager.integrations.base import (
    ExecutionContextExtractor,
    FrameworkAdapter,
    HumanInTheLoopBridge,
    LearnedContextInjector,
    render_learned_context,
)

if TYPE_CHECKING:
    from feedback_manager import FeedbackManager
    from feedback_manager.integrations.base import LearnedArtifactRetriever
    from feedback_manager.retrieval import RetrievalResult


@dataclass(frozen=True)
class RunInfo:  # Replace with the framework's run or config type.
    session_id: str
    run_id: str
    agent_name: str
    step: str | None = None


@dataclass(frozen=True)
class Pause:  # Replace with the framework's interrupt type.
    key: str
    question: Any


@dataclass(frozen=True)
class Resume:  # Replace with the framework's resume type.
    value: Any
    key: str | None = None


class ToyContextExtractor(ExecutionContextExtractor[RunInfo]):
    def extract(
        self,
        source: RunInfo | None,
        *,
        node_id: str | None = None,
        tool_call_id: str | None = None,
        generation_id: str | None = None,
        message_id: str | None = None,
        interrupt_id: str | None = None,
    ) -> ExecutionContext:
        # Map the framework's identifiers onto ExecutionContext fields.
        if source is None:
            return ExecutionContext(
                node_id=node_id,
                tool_call_id=tool_call_id,
                generation_id=generation_id,
                message_id=message_id,
                interrupt_id=interrupt_id,
            )
        return ExecutionContext(
            thread_id=source.session_id,
            run_id=source.run_id,
            agent_id=source.agent_name,
            node_id=node_id or source.step,
            tool_call_id=tool_call_id,
            generation_id=generation_id,
            message_id=message_id,
            interrupt_id=interrupt_id,
        )


class ToyHumanInTheLoopBridge(HumanInTheLoopBridge[Pause, Resume]):
    def interrupt_id(self, interrupt: Pause) -> str:
        return interrupt.key

    def interrupt_value(self, interrupt: Pause) -> Any:
        return interrupt.question

    def resume_command(self, response: Any, *, interrupt_id: str | None = None) -> Resume:
        return Resume(value=response, key=interrupt_id)


class ToyContextInjector(LearnedContextInjector[dict[str, str]]):
    def render(self, result: RetrievalResult) -> dict[str, str]:
        # Keep render_learned_context: it delimits the lessons as reference data.
        return {"role": "system", "content": render_learned_context(result)}


class ToyAdapter(FrameworkAdapter):
    name: ClassVar[str] = "toy"  # Replace with the framework's name.

    def context_extractor(self) -> ToyContextExtractor:
        return ToyContextExtractor()

    def human_in_the_loop(self, manager: FeedbackManager) -> ToyHumanInTheLoopBridge:
        return ToyHumanInTheLoopBridge(manager)

    def context_injector(self, retriever: LearnedArtifactRetriever) -> ToyContextInjector:
        return ToyContextInjector(retriever)
