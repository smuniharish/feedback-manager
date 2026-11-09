"""Extract explicit learning signals from feedback and execution outcomes."""

from __future__ import annotations

import json
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, model_validator

from feedback_manager.core.categories import FeedbackCategory
from feedback_manager.core.sources import FeedbackSource
from feedback_manager.core.status import FeedbackStatus
from feedback_manager.learning.errors import LearningScopeError
from feedback_manager.learning.models import (
    ArtifactScope,
    Evidence,
    ExecutionOutcome,
    LearningSignal,
    SignalType,
)

if TYPE_CHECKING:
    from feedback_manager.core._types import JsonObject
    from feedback_manager.core.events import FeedbackEvent


class ExtractionDisposition(StrEnum):
    """Whether a feedback event yielded a learning signal."""

    EXTRACTED = "extracted"
    IGNORED = "ignored"


class SignalExtraction(BaseModel):
    """The signal or an explicit reason that feedback was not learnable."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    disposition: ExtractionDisposition
    signal: LearningSignal | None = None
    reason: str = ""

    @model_validator(mode="after")
    def _validate_result(self) -> SignalExtraction:
        if self.disposition is ExtractionDisposition.EXTRACTED and self.signal is None:
            raise ValueError("an extracted result requires a signal")
        if self.disposition is ExtractionDisposition.IGNORED and (
            self.signal is not None or not self.reason
        ):
            raise ValueError("an ignored result requires a reason and no signal")
        return self


def _text(payload: JsonObject, *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _signal_id(scope: ArtifactScope, source_id: str, kind: SignalType) -> str:
    return json.dumps(
        [scope.tenant_id, scope.namespace, source_id, kind.value], separators=(",", ":")
    )


class LearningSignalExtractor:
    """Extract only explicit feedback and outcomes, without interpreting instructions.

    Feedback with no execution ID receives one shared ``unverified`` independence
    key. Multiple such records cannot alone satisfy a policy that requires
    independent evidence.
    """

    def extract(self, feedback: FeedbackEvent, *, scope: ArtifactScope) -> SignalExtraction:
        """Extract a signal, or return an explanation of why the event was ignored.

        ``scope`` must be established by the host. A tenant already recorded on
        the feedback cannot be overridden by a different scope.
        """
        context = feedback.execution_context
        if context is not None and context.tenant_id not in (None, scope.tenant_id):
            raise LearningScopeError("feedback tenant does not match the requested learning scope")
        if feedback.status is FeedbackStatus.CREATED:
            return SignalExtraction(
                disposition=ExtractionDisposition.IGNORED,
                reason="feedback has not been accepted by a feedback store",
            )
        hitl_decision = (
            feedback.category == FeedbackCategory.REQUEST_FOR_HUMAN
            and feedback.resolution is not None
            and isinstance(feedback.resolution.get("approved"), bool)
            and feedback.status in (FeedbackStatus.RESOLVED, FeedbackStatus.REJECTED)
        )
        if (
            feedback.status
            in (
                FeedbackStatus.REJECTED,
                FeedbackStatus.CANCELLED,
                FeedbackStatus.EXPIRED,
            )
            and not hitl_decision
        ):
            return SignalExtraction(
                disposition=ExtractionDisposition.IGNORED,
                reason="feedback was rejected, cancelled, or expired",
            )

        kind: SignalType
        content: str | None
        source = feedback.source
        improvement = _text(feedback.payload, "improvement")
        constraint = _text(feedback.payload, "constraint")
        if hitl_decision and feedback.resolution is not None:
            approved = feedback.resolution["approved"] is True
            kind = SignalType.APPROVAL if approved else SignalType.REJECTION
            content = _text(feedback.resolution, "response")
            if content is None:
                decision = "approved" if approved else "rejected"
                content = f"Human {decision} {feedback.target.type} {feedback.target.id}"
            source = FeedbackSource.HUMAN
        elif (
            improvement is not None
            and constraint is not None
            and feedback.source == FeedbackSource.HUMAN
        ):
            return SignalExtraction(
                disposition=ExtractionDisposition.IGNORED,
                reason="feedback contains both an improvement and a constraint",
            )
        elif improvement is not None and feedback.source == FeedbackSource.HUMAN:
            kind, content = SignalType.IMPROVEMENT, improvement
        elif constraint is not None and feedback.source == FeedbackSource.HUMAN:
            kind, content = SignalType.CONSTRAINT, constraint
        elif feedback.category == FeedbackCategory.CORRECTION:
            kind = SignalType.CORRECTION
            content = _text(feedback.payload, "corrected_text", "correction")
        elif feedback.category == FeedbackCategory.APPROVAL:
            kind = SignalType.APPROVAL
            content = _text(feedback.payload, "reason", "message", "response")
        elif feedback.category == FeedbackCategory.REJECTION:
            kind = SignalType.REJECTION
            content = _text(feedback.payload, "reason", "message", "response")
        elif feedback.category in (FeedbackCategory.FAILURE, FeedbackCategory.TIMEOUT):
            kind = SignalType.FAILURE
            content = _text(feedback.payload, "error", "error_type", "message")
        elif feedback.category == FeedbackCategory.COMPLETION:
            kind = SignalType.SUCCESS
            content = _text(feedback.payload, "summary", "outcome")
        else:
            return SignalExtraction(
                disposition=ExtractionDisposition.IGNORED,
                reason="feedback category does not contain a supported learning signal",
            )
        if content is None:
            return SignalExtraction(
                disposition=ExtractionDisposition.IGNORED,
                reason="feedback has no supported non-empty signal content",
            )

        execution_id = None if context is None else context.run_id
        signal_id = uuid5(NAMESPACE_URL, _signal_id(scope, str(feedback.feedback_id), kind))
        evidence = Evidence(
            evidence_id=signal_id,
            feedback_id=feedback.feedback_id,
            execution_id=execution_id,
            source=source,
            independence_key="unverified" if execution_id is None else f"execution:{execution_id}",
            observed_at=feedback.created_at,
        )
        signal = LearningSignal(
            signal_id=signal_id,
            kind=kind,
            scope=scope,
            content=content,
            evidence=evidence,
            occurred_at=feedback.created_at,
        )
        return SignalExtraction(disposition=ExtractionDisposition.EXTRACTED, signal=signal)

    def extract_outcome(self, outcome: ExecutionOutcome) -> LearningSignal:
        """Convert an explicitly reported execution outcome into a signal."""
        kind = SignalType.SUCCESS if outcome.success else SignalType.FAILURE
        signal_id = uuid5(NAMESPACE_URL, _signal_id(outcome.scope, outcome.execution_id, kind))
        evidence = Evidence(
            evidence_id=signal_id,
            execution_id=outcome.execution_id,
            source=outcome.source,
            independence_key=f"execution:{outcome.execution_id}",
            observed_at=outcome.occurred_at,
        )
        return LearningSignal(
            signal_id=signal_id,
            kind=kind,
            scope=outcome.scope,
            content=outcome.description,
            evidence=evidence,
            occurred_at=outcome.occurred_at,
        )


__all__ = ["ExtractionDisposition", "LearningSignalExtractor", "SignalExtraction"]
