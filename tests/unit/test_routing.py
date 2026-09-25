"""Unit tests for routing rules and the default router."""

from __future__ import annotations

from dataclasses import dataclass

from feedback_manager.contracts.handler import (
    FeedbackContext,
    FeedbackHandler,
    FeedbackHandlerResult,
)
from feedback_manager.core import (
    FeedbackCategory,
    FeedbackEvent,
    FeedbackSource,
    FeedbackTarget,
    FeedbackTargetType,
)
from feedback_manager.routing.default_router import DefaultFeedbackRouter
from feedback_manager.routing.rules import (
    RoutingRule,
    all_of,
    by_category,
    by_source,
    by_target_type,
)


@dataclass
class RecordingHandler(FeedbackHandler):
    name: str
    seen: list[FeedbackEvent]

    async def handle(
        self, feedback: FeedbackEvent, context: FeedbackContext
    ) -> FeedbackHandlerResult:
        self.seen.append(feedback)
        return FeedbackHandlerResult(handled=True, detail=self.name)


def _event(**overrides: object) -> FeedbackEvent:
    defaults: dict[str, object] = dict(
        source=FeedbackSource.HUMAN,
        category=FeedbackCategory.CORRECTION,
        target=FeedbackTarget(type=FeedbackTargetType.GENERATION, id="gen-1"),
    )
    defaults.update(overrides)
    return FeedbackEvent(**defaults)  # type: ignore[arg-type]


async def test_matching_rule_selects_its_handlers() -> None:
    seen: list[FeedbackEvent] = []
    handler = RecordingHandler("human", seen)
    router = DefaultFeedbackRouter(
        [RoutingRule(predicate=by_source(FeedbackSource.HUMAN), handlers=(handler,))]
    )
    event = _event()
    matched = await router.route(event)
    assert matched == [handler]


async def test_non_matching_rule_falls_back_to_defaults() -> None:
    seen: list[FeedbackEvent] = []
    default_handler = RecordingHandler("default", seen)
    router = DefaultFeedbackRouter(
        [RoutingRule(predicate=by_source(FeedbackSource.TOOL), handlers=())],
        default_handlers=(default_handler,),
    )
    matched = await router.route(_event(source=FeedbackSource.HUMAN))
    assert matched == [default_handler]


async def test_multiple_matching_rules_are_deduplicated_by_identity() -> None:
    seen: list[FeedbackEvent] = []
    shared = RecordingHandler("shared", seen)
    router = DefaultFeedbackRouter(
        [
            RoutingRule(predicate=by_source(FeedbackSource.HUMAN), handlers=(shared,)),
            RoutingRule(predicate=by_category(FeedbackCategory.CORRECTION), handlers=(shared,)),
        ]
    )
    matched = await router.route(_event())
    assert matched == [shared]


async def test_all_of_predicate_combinator() -> None:
    predicate = all_of(
        by_source(FeedbackSource.HUMAN), by_target_type(FeedbackTargetType.GENERATION)
    )
    assert predicate(_event())
    assert not predicate(_event(source=FeedbackSource.TOOL))


async def test_add_rule_after_construction() -> None:
    seen: list[FeedbackEvent] = []
    handler = RecordingHandler("late", seen)
    router = DefaultFeedbackRouter()
    router.add_rule(RoutingRule(predicate=by_source(FeedbackSource.HUMAN), handlers=(handler,)))
    matched = await router.route(_event())
    assert matched == [handler]


async def test_no_match_and_no_defaults_returns_empty() -> None:
    router = DefaultFeedbackRouter()
    matched = await router.route(_event())
    assert matched == []
