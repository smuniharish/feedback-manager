"""Bounded deterministic grouping and similarity discovery for signals."""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, model_validator

from feedback_manager.learning.errors import LearningValidationError
from feedback_manager.learning.models import ArtifactScope, LearningSignal, SignalType

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID


def _normalize(content: str, *, case_sensitive: bool) -> str:
    normalized = " ".join(content.split())
    return normalized if case_sensitive else normalized.casefold()


def _fingerprint(
    scope: ArtifactScope, kind: SignalType, normalized: str, *, case_sensitive: bool
) -> str:
    body = [scope.model_dump(mode="json"), kind.value, normalized, case_sensitive]
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


class PatternGroup(BaseModel):
    """Signals with matching scope, kind and deterministic normalized content."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pattern_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    scope: ArtifactScope
    kind: SignalType
    normalized_content: str = Field(min_length=1)
    case_sensitive: bool
    signals: tuple[LearningSignal, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_group(self) -> PatternGroup:
        expected = _fingerprint(
            self.scope, self.kind, self.normalized_content, case_sensitive=self.case_sensitive
        )
        if self.pattern_id != expected:
            raise ValueError("pattern ID does not match its scope and content")
        signal_ids = set()
        for signal in self.signals:
            if (
                signal.scope != self.scope
                or signal.kind != self.kind
                or _normalize(signal.content, case_sensitive=self.case_sensitive)
                != self.normalized_content
            ):
                raise ValueError("pattern signals must have matching scope, kind and content")
            if signal.signal_id in signal_ids:
                raise ValueError("pattern signal IDs must be unique")
            signal_ids.add(signal.signal_id)
        return self


class PatternSimilarity(BaseModel):
    """A suggested relationship between groups, not merged evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    left_pattern_id: str
    right_pattern_id: str
    score: float = Field(ge=0.0, le=1.0)


class PatternMiner:
    """Group exact normalized signals and suggest similar groups without merging them."""

    def __init__(self, *, max_signals: int = 1000, case_sensitive: bool = True) -> None:
        if isinstance(max_signals, bool) or not isinstance(max_signals, int) or max_signals < 1:
            raise LearningValidationError("max_signals must be a positive integer")
        self.max_signals = max_signals
        self.case_sensitive = case_sensitive

    def mine(self, signals: Sequence[LearningSignal]) -> tuple[PatternGroup, ...]:
        """Group at most ``max_signals`` records, rejecting conflicting duplicate IDs."""
        if len(signals) > self.max_signals:
            raise LearningValidationError("signal batch exceeds the configured maximum")
        seen: dict[UUID, LearningSignal] = {}
        groups: dict[str, list[LearningSignal]] = {}
        for signal in signals:
            previous = seen.get(signal.signal_id)
            if previous is not None:
                if previous != signal:
                    raise LearningValidationError("duplicate signal ID has conflicting content")
                continue
            seen[signal.signal_id] = signal
            normalized = _normalize(signal.content, case_sensitive=self.case_sensitive)
            if not normalized:
                raise LearningValidationError("signal content must not be blank")
            fingerprint = _fingerprint(
                signal.scope, signal.kind, normalized, case_sensitive=self.case_sensitive
            )
            groups.setdefault(fingerprint, []).append(signal)
        results: list[PatternGroup] = []
        for fingerprint, grouped in sorted(groups.items()):
            ordered = tuple(sorted(grouped, key=lambda item: item.signal_id.hex))
            first = ordered[0]
            results.append(
                PatternGroup(
                    pattern_id=fingerprint,
                    scope=first.scope,
                    kind=first.kind,
                    normalized_content=_normalize(
                        first.content, case_sensitive=self.case_sensitive
                    ),
                    case_sensitive=self.case_sensitive,
                    signals=ordered,
                )
            )
        return tuple(results)

    def similar(
        self,
        patterns: Sequence[PatternGroup],
        *,
        threshold: float = 0.8,
        limit: int = 100,
    ) -> tuple[PatternSimilarity, ...]:
        """Suggest related groups in the same scope using token Jaccard similarity.

        Suggestions never merge their evidence. They require separate review.
        """
        if (
            isinstance(threshold, bool)
            or not isinstance(threshold, (int, float))
            or not math.isfinite(threshold)
            or not 0 < threshold <= 1
        ):
            raise LearningValidationError("similarity threshold must be between 0 and 1")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise LearningValidationError("similarity limit must be between 1 and 1000")
        if len(patterns) > 200:
            raise LearningValidationError("similarity comparison is limited to 200 patterns")
        if len({pattern.pattern_id for pattern in patterns}) != len(patterns):
            raise LearningValidationError("similarity comparison requires distinct patterns")
        suggestions: list[PatternSimilarity] = []
        for index, left in enumerate(patterns):
            left_tokens = set(re.findall(r"\w+", left.normalized_content.casefold()))
            for right in patterns[index + 1 :]:
                if left.scope != right.scope or left.kind != right.kind:
                    continue
                right_tokens = set(re.findall(r"\w+", right.normalized_content.casefold()))
                union = left_tokens | right_tokens
                score = len(left_tokens & right_tokens) / len(union) if union else 0.0
                if score >= threshold:
                    first, second = sorted((left.pattern_id, right.pattern_id))
                    suggestions.append(
                        PatternSimilarity(
                            left_pattern_id=first, right_pattern_id=second, score=score
                        )
                    )
        suggestions.sort(
            key=lambda item: (-item.score, item.left_pattern_id, item.right_pattern_id)
        )
        return tuple(suggestions[:limit])


__all__ = ["PatternGroup", "PatternMiner", "PatternSimilarity"]
