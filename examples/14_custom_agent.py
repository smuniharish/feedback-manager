"""Integrate a hand-written agent loop with no agent framework installed.

The custom adapter records a tool failure, a human-in-the-loop approval, and
injects reviewed learned context into the next prompt. The same steps work
for any framework: only the adapter changes.
"""

import asyncio

from feedback_manager import FeedbackManager, FeedbackQuery, FeedbackTarget, FeedbackTargetType
from feedback_manager.integrations import FrameworkAdapter, capture_tool_feedback
from feedback_manager.integrations.frameworks.custom import CustomAgentAdapter, PendingInterrupt
from feedback_manager.learning import ArtifactScope
from feedback_manager.learning.engine import LearningEngine
from feedback_manager.learning.signals import LearningSignalExtractor
from feedback_manager.retrieval import RetrievalRequest
from feedback_manager.storage.artifacts import InMemoryArtifactRepository


async def search(query: str) -> str:
    raise TimeoutError(f"search backend did not answer for {query!r}")


async def main() -> None:
    manager = FeedbackManager(learning=False)
    adapter: FrameworkAdapter = CustomAgentAdapter()
    scope = ArtifactScope(tenant_id="acme", agent_id="refund-agent")

    # 1. Your agent passes plain dictionaries around; the extractor reads them.
    state = {"tenant_id": "acme", "agent_id": "refund-agent", "run_id": "run-7"}
    context = adapter.context_extractor().extract(state, node_id="lookup")

    # 2. Record a failing tool call without changing the agent's error handling.
    try:
        async with capture_tool_feedback(
            manager,
            tool_call_id="call-1",
            tool_name="search",
            execution_context=context,
            classifier=adapter.failure_classifier(),
        ):
            await search("order 42")
    except TimeoutError:
        pass

    # 3. Pause for a person, record the request, and resume with their answer.
    bridge = adapter.human_in_the_loop(manager)
    pause = PendingInterrupt(id="approve-refund-42", value={"question": "Refund 900 EUR?"})
    request = await bridge.request(
        target=FeedbackTarget(type=FeedbackTargetType.NODE, id="approve-refund"),
        interrupt=pause,
        execution_context=context,
    )
    resume = bridge.resume_command("approved", interrupt_id=pause.id)
    decision = await bridge.resolve(request.feedback_id, response=resume.response, approved=True)

    for event in await manager.query(FeedbackQuery(correlation_id="run-7")):
        print(f"{event.source:<6} {event.category:<20} {event.target.id:<18} {event.status}")
    print(f"resume value: {resume}")
    print(f"decision: {decision.resolution}")

    # 4. A reviewed correction becomes context for the agent's next task.
    engine = LearningEngine(InMemoryArtifactRepository())
    extractor = LearningSignalExtractor()
    signals = []
    for run in ("run-1", "run-2"):
        event = await manager.submit(
            source="human",
            category="comment",
            target=FeedbackTarget(type=FeedbackTargetType.GENERATION, id=run),
            payload={"improvement": "Retry search once before asking a person."},
            execution_context=adapter.context_extractor().extract(
                {"tenant_id": "acme", "run_id": run}
            ),
        )
        extracted = extractor.extract(event, scope=scope)
        if extracted.signal is None:
            raise RuntimeError(f"Feedback could not be learned: {extracted.reason}")
        signals.append(extracted.signal)
    candidate = (await engine.process(signals)).artifacts[0]
    await engine.request_review(scope, candidate.artifact_id)
    await engine.review(scope, candidate.artifact_id, reviewer="lead", approved=True)
    await engine.activate(scope, candidate.artifact_id)

    learned = await adapter.context_injector(engine).build(
        RetrievalRequest(scope=scope, agent_id="refund-agent", task="search")
    )
    print(learned)


if __name__ == "__main__":
    asyncio.run(main())
