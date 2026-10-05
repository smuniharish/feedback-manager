"""Routing, correlation, the audit handler, logging, and observability sinks."""

from __future__ import annotations

import logging
from uuid import uuid4

import pytest

from feedback_manager import ExecutionContext, FeedbackEvent, FeedbackTarget
from feedback_manager._logging import get_logger
from feedback_manager.contracts import FeedbackHandler, FeedbackHandlerResult
from feedback_manager.correlation import DefaultFeedbackCorrelator, default_correlation_id
from feedback_manager.handlers import AuditFeedbackHandler
from feedback_manager.observability import (
    FEEDBACK_FAILED,
    FEEDBACK_RECEIVED,
    LoggingObservabilitySink,
    NoOpObservabilitySink,
    ObservabilityEvent,
    ObservabilitySink,
)
from feedback_manager.routing import (
    DefaultFeedbackRouter,
    RoutingRule,
    all_of,
    any_of,
    by_category,
    by_source,
    by_target_type,
)


def _event(**overrides: object) -> FeedbackEvent:
    fields: dict[str, object] = {
        "source": "human",
        "category": "correction",
        "target": FeedbackTarget(type="generation", id="gen-1"),
    }
    fields.update(overrides)
    return FeedbackEvent.model_validate(fields)


class Handler(FeedbackHandler):
    def __init__(self, name: str) -> None:
        self.name = name

    async def handle(self, feedback: FeedbackEvent) -> FeedbackHandlerResult:
        return FeedbackHandlerResult(handled=True, detail=self.name)


class TestRouting:
    def test_predicates(self) -> None:
        event = _event()

        assert by_source("human")(event)
        assert not by_source("tool")(event)
        assert by_category("correction")(event)
        assert by_target_type("generation")(event)
        assert any_of(by_source("tool"), by_category("correction"))(event)
        assert not any_of(by_source("tool"), by_category("rating"))(event)
        assert all_of(by_source("human"), by_target_type("generation"))(event)
        assert not all_of(by_source("human"), by_target_type("node"))(event)

    def test_rules_store_handlers_as_a_tuple(self) -> None:
        handler = Handler("a")

        rule = RoutingRule(predicate=by_source("human"), handlers=[handler], name="humans")

        assert rule.handlers == (handler,)
        assert rule.name == "humans"

    async def test_matching_rules_contribute_handlers_in_order_once(self) -> None:
        first, second, fallback = Handler("first"), Handler("second"), Handler("fallback")
        router = DefaultFeedbackRouter(
            [
                RoutingRule(predicate=by_source("human"), handlers=(first, second)),
                RoutingRule(predicate=by_source("tool"), handlers=(fallback,)),
                RoutingRule(predicate=by_category("correction"), handlers=(second, first)),
            ],
            default_handlers=(fallback,),
        )

        assert await router.route(_event()) == (first, second)

    async def test_defaults_apply_when_no_rule_matches(self) -> None:
        fallback = Handler("fallback")
        router = DefaultFeedbackRouter(default_handlers=[fallback])

        assert await router.route(_event()) == (fallback,)
        assert await DefaultFeedbackRouter().route(_event()) == ()

    async def test_rules_can_be_added_later(self) -> None:
        handler = Handler("late")
        router = DefaultFeedbackRouter()
        router.add_rule(RoutingRule(predicate=by_source("human"), handlers=(handler,)))

        assert await router.route(_event()) == (handler,)


class TestCorrelation:
    @pytest.mark.parametrize(
        ("context", "expected"),
        [
            (ExecutionContext(run_id="run", thread_id="thread", checkpoint_id="cp"), "run"),
            (ExecutionContext(thread_id="thread", checkpoint_id="cp"), "thread"),
            (ExecutionContext(checkpoint_id="cp"), "cp"),
        ],
    )
    async def test_most_specific_identifier_wins(
        self, context: ExecutionContext, expected: str
    ) -> None:
        correlation_id = await DefaultFeedbackCorrelator().correlate(
            _event(execution_context=context)
        )

        assert correlation_id == expected

    @pytest.mark.parametrize("context", [None, ExecutionContext(node_id="node")])
    def test_uncorrelated_feedback_is_its_own_group(self, context: ExecutionContext | None) -> None:
        event = _event(execution_context=context)

        assert default_correlation_id(event) == str(event.feedback_id)


class TestAuditHandler:
    async def test_logs_identity_but_never_the_payload(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        event = _event(payload={"secret": "s3cr3t"}, correlation_id="run-1")

        with caplog.at_level(logging.INFO, logger="feedback_manager.audit"):
            result = await AuditFeedbackHandler().handle(event)

        assert result == FeedbackHandlerResult(handled=True, detail="audited")
        assert str(event.feedback_id) in caplog.text
        assert "correlation_id='run-1'" in caplog.text
        assert "s3cr3t" not in caplog.text


class TestLogging:
    def test_disabled_levels_are_dropped_before_rendering(
        self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rendered: list[str] = []

        def render(*args: object) -> str:
            rendered.append("called")
            return "message"

        monkeypatch.setattr("feedback_manager._logging._RENDERER", render)
        logger = get_logger("feedback_manager.tests.levels")

        with caplog.at_level(logging.WARNING, logger="feedback_manager.tests.levels"):
            logger.info("dropped", value=1)
            logger.warning("kept", value=2)

        assert rendered == ["called"]
        assert [record.getMessage() for record in caplog.records] == ["message"]

    def test_records_are_key_value_rendered_with_standard_tracebacks(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        logger = get_logger("feedback_manager.tests.render")
        error = ValueError("broken")

        with caplog.at_level(logging.INFO, logger="feedback_manager.tests.render"):
            logger.info("plain", count=2)
            logger.error("failed", exc_info=error)

        plain, failed = caplog.records
        assert plain.getMessage() == "event='plain' count=2"
        assert plain.exc_info is None
        assert failed.getMessage() == "event='failed'"
        assert failed.exc_info is not None
        assert failed.exc_info[1] is error


class TestObservabilitySinks:
    def test_sinks_satisfy_the_protocol(self) -> None:
        assert isinstance(LoggingObservabilitySink(), ObservabilitySink)
        assert isinstance(NoOpObservabilitySink(), ObservabilitySink)

    def test_noop_sink_discards(self) -> None:
        NoOpObservabilitySink().emit(
            ObservabilityEvent(name=FEEDBACK_RECEIVED, feedback_id=uuid4())
        )

    def test_logging_sink_levels(self, caplog: pytest.LogCaptureFixture) -> None:
        sink = LoggingObservabilitySink("feedback_manager.tests.sink")
        received = ObservabilityEvent(
            name=FEEDBACK_RECEIVED, feedback_id=uuid4(), attributes={"source": "human"}
        )
        failed = ObservabilityEvent(
            name=FEEDBACK_FAILED, feedback_id=uuid4(), attributes={"stage": "handler"}
        )

        with caplog.at_level(logging.INFO, logger="feedback_manager.tests.sink"):
            sink.emit(received)
            sink.emit(failed)

        assert [record.levelno for record in caplog.records] == [logging.INFO, logging.WARNING]
        assert "event='feedback.received'" in caplog.records[0].getMessage()
        assert "stage='handler'" in caplog.records[1].getMessage()
        assert received.occurred_at.tzinfo is not None
