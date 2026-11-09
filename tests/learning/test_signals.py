import pytest
from pydantic import ValidationError

from feedback_manager import (
    ExecutionContext,
    FeedbackCategory,
    FeedbackEvent,
    FeedbackSource,
    FeedbackStatus,
    FeedbackTarget,
    FeedbackTargetType,
)
from feedback_manager.learning import (
    ArtifactScope,
    ExecutionOutcome,
    LearningScopeError,
    SignalType,
)
from feedback_manager.learning.signals import (
    ExtractionDisposition,
    LearningSignalExtractor,
    SignalExtraction,
)

SCOPE = ArtifactScope(tenant_id="tenant", agent_id="agent")


def feedback(
    category: FeedbackCategory,
    payload: dict[str, str],
    *,
    source: FeedbackSource = FeedbackSource.HUMAN,
    status: FeedbackStatus = FeedbackStatus.RECEIVED,
    tenant: str | None = "tenant",
    run: str | None = "run-1",
) -> FeedbackEvent:
    return FeedbackEvent(
        source=source,
        category=category,
        target=FeedbackTarget(type=FeedbackTargetType.GENERATION, id="generation"),
        payload=payload,
        execution_context=ExecutionContext(tenant_id=tenant, run_id=run),
        status=status,
    )


@pytest.mark.parametrize(
    ("category", "payload", "expected"),
    [
        (FeedbackCategory.CORRECTION, {"corrected_text": "corrected"}, SignalType.CORRECTION),
        (FeedbackCategory.CORRECTION, {"correction": "corrected"}, SignalType.CORRECTION),
        (FeedbackCategory.APPROVAL, {"reason": "correct"}, SignalType.APPROVAL),
        (FeedbackCategory.REJECTION, {"message": "incorrect"}, SignalType.REJECTION),
        (FeedbackCategory.FAILURE, {"error": "failed"}, SignalType.FAILURE),
        (FeedbackCategory.TIMEOUT, {"error_type": "TimeoutError"}, SignalType.FAILURE),
        (FeedbackCategory.COMPLETION, {"summary": "done"}, SignalType.SUCCESS),
        (FeedbackCategory.COMMENT, {"improvement": "validate first"}, SignalType.IMPROVEMENT),
        (FeedbackCategory.COMMENT, {"constraint": "avoid tool x"}, SignalType.CONSTRAINT),
    ],
)
def test_extract_supported_feedback(
    category: FeedbackCategory, payload: dict[str, str], expected: SignalType
) -> None:
    event = feedback(category, payload)
    extractor = LearningSignalExtractor()
    result = extractor.extract(event, scope=SCOPE)
    assert result.disposition is ExtractionDisposition.EXTRACTED
    assert result.signal is not None
    assert result.signal.kind is expected
    assert result.signal.evidence.feedback_id == event.feedback_id
    assert result.signal.evidence.execution_id == "run-1"
    assert result.signal.evidence.independence_key == "execution:run-1"
    assert extractor.extract(event, scope=SCOPE) == result


def test_unsupported_invalid_and_rejected_feedback_have_explicit_reasons() -> None:
    extractor = LearningSignalExtractor()
    for event in (
        feedback(FeedbackCategory.COMMENT, {"message": "comment"}),
        feedback(FeedbackCategory.CORRECTION, {"corrected_text": " "}),
        feedback(
            FeedbackCategory.CORRECTION, {"corrected_text": "x"}, status=FeedbackStatus.CREATED
        ),
        feedback(
            FeedbackCategory.CORRECTION, {"corrected_text": "x"}, status=FeedbackStatus.REJECTED
        ),
        feedback(
            FeedbackCategory.CORRECTION, {"corrected_text": "x"}, status=FeedbackStatus.CANCELLED
        ),
        feedback(
            FeedbackCategory.CORRECTION, {"corrected_text": "x"}, status=FeedbackStatus.EXPIRED
        ),
        feedback(FeedbackCategory.COMMENT, {"improvement": "x"}, source=FeedbackSource.AGENT),
        feedback(FeedbackCategory.COMMENT, {"improvement": "x", "constraint": "y"}),
    ):
        result = extractor.extract(event, scope=SCOPE)
        assert result.disposition is ExtractionDisposition.IGNORED
        assert result.signal is None
        assert result.reason


def test_scope_mismatch_is_rejected_and_unverified_sources_do_not_gain_independence() -> None:
    extractor = LearningSignalExtractor()
    with pytest.raises(LearningScopeError):
        extractor.extract(
            feedback(FeedbackCategory.CORRECTION, {"correction": "x"}, tenant="other"), scope=SCOPE
        )
    result = extractor.extract(
        feedback(FeedbackCategory.CORRECTION, {"correction": "x"}, tenant=None, run=None),
        scope=SCOPE,
    )
    assert result.signal is not None
    assert result.signal.evidence.independence_key == "unverified"


@pytest.mark.parametrize(
    ("approved", "status", "expected"),
    [
        (True, FeedbackStatus.RESOLVED, SignalType.APPROVAL),
        (False, FeedbackStatus.REJECTED, SignalType.REJECTION),
    ],
)
def test_hitl_resolutions_produce_human_decision_signals(
    approved: bool, status: FeedbackStatus, expected: SignalType
) -> None:
    event = feedback(
        FeedbackCategory.REQUEST_FOR_HUMAN, {}, source=FeedbackSource.SYSTEM, status=status
    ).model_copy(update={"resolution": {"response": {"decision": approved}, "approved": approved}})
    result = LearningSignalExtractor().extract(event, scope=SCOPE)
    assert result.signal is not None
    assert result.signal.kind is expected
    assert result.signal.evidence.source == FeedbackSource.HUMAN
    assert result.signal.content.startswith("Human ")


def test_hitl_text_response_is_used_for_the_decision() -> None:
    event = feedback(
        FeedbackCategory.REQUEST_FOR_HUMAN,
        {},
        source=FeedbackSource.SYSTEM,
        status=FeedbackStatus.RESOLVED,
    ).model_copy(update={"resolution": {"response": "Looks good", "approved": True}})
    result = LearningSignalExtractor().extract(event, scope=SCOPE)
    assert result.signal is not None
    assert result.signal.content == "Looks good"


def test_execution_outcomes_produce_deterministic_signals() -> None:
    extractor = LearningSignalExtractor()
    outcome = ExecutionOutcome(execution_id="run", scope=SCOPE, success=True, description="done")
    signal = extractor.extract_outcome(outcome)
    assert signal == extractor.extract_outcome(outcome)
    assert signal.kind is SignalType.SUCCESS
    assert signal.evidence.independence_key == "execution:run"
    assert extractor.extract_outcome(outcome.model_copy(update={"success": False})).kind is (
        SignalType.FAILURE
    )


def test_signal_extraction_model_rejects_inconsistent_results() -> None:
    with pytest.raises(ValidationError):
        SignalExtraction(disposition=ExtractionDisposition.EXTRACTED)
    with pytest.raises(ValidationError):
        SignalExtraction(disposition=ExtractionDisposition.IGNORED)
