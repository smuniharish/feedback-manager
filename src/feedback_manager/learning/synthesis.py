"""Evidence-grounded synthesis of related learning patterns into candidates.

Synthesis never widens scope, never treats contradicting evidence as support, and
only produces candidates. Human review and explicit activation remain separate.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, model_validator

from feedback_manager.learning.errors import LearningValidationError
from feedback_manager.learning.models import ArtifactScope, SignalType
from feedback_manager.learning.patterns import PatternGroup, PatternSimilarity

if TYPE_CHECKING:
    from collections.abc import Sequence

    from feedback_manager.core._types import Identifier

_MAX_CLUSTER = 50
_MAX_CONTENT = 10000


class PatternSupport(BaseModel):
    """One pattern and its independent supporting and contradicting sources."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pattern: PatternGroup
    independent_support: int = Field(ge=0)
    independent_contradictions: int = Field(ge=0)

    @classmethod
    def of(cls, pattern: PatternGroup) -> PatternSupport:
        """Count distinct valid independence keys for and against ``pattern``."""
        support = {
            signal.evidence.independence_key
            for signal in pattern.signals
            if signal.evidence.valid and not signal.evidence.contradicts
        }
        against = {
            signal.evidence.independence_key
            for signal in pattern.signals
            if signal.evidence.valid and signal.evidence.contradicts
        }
        return cls(
            pattern=pattern,
            independent_support=len(support),
            independent_contradictions=len(against),
        )


class SynthesisInput(BaseModel):
    """A bounded cluster of related patterns sharing one scope and signal kind."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scope: ArtifactScope
    kind: SignalType
    patterns: tuple[PatternSupport, ...] = Field(min_length=2, max_length=_MAX_CLUSTER)

    @model_validator(mode="after")
    def _validate_cluster(self) -> SynthesisInput:
        identifiers = [item.pattern.pattern_id for item in self.patterns]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("synthesis patterns must be distinct")
        for item in self.patterns:
            if item.pattern.scope != self.scope or item.pattern.kind != self.kind:
                raise ValueError("synthesis patterns must share scope and kind")
        return self


class SynthesisDraft(BaseModel):
    """Synthesized text and the pattern IDs it is grounded in."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str = Field(min_length=1, max_length=512)
    content: str = Field(min_length=1, max_length=_MAX_CONTENT)
    cited_pattern_ids: tuple[str, ...] = Field(min_length=1)


class PatternSynthesizer(ABC):
    """Combine a cluster of related patterns into one grounded candidate draft."""

    @property
    @abstractmethod
    def name(self) -> Identifier:
        """Return a stable identifier recorded in provenance and candidate IDs."""

    @abstractmethod
    async def synthesize(self, request: SynthesisInput) -> SynthesisDraft:
        """Return a draft citing only supported patterns from ``request``."""


class DeterministicSynthesizer(PatternSynthesizer):
    """Restate supported statements verbatim, ordered by independent support.

    No text is generated beyond the reported statements and their support counts.
    Patterns with no independent support are listed only as contradicted reports.
    """

    @property
    def name(self) -> Identifier:
        """Return the synthesizer identifier."""
        return "deterministic-v1"

    async def synthesize(self, request: SynthesisInput) -> SynthesisDraft:
        """Build a bounded draft from the supported patterns in ``request``."""
        ordered = sorted(
            request.patterns,
            key=lambda item: (
                -item.independent_support,
                item.independent_contradictions,
                item.pattern.pattern_id,
            ),
        )
        supported = [item for item in ordered if item.independent_support]
        if not supported:
            raise LearningValidationError("synthesis requires independently supported patterns")
        header = "Related reports in this scope, ordered by independent support:"
        lines = [header]
        cited: list[str] = []
        size = len(header)
        for item in supported:
            line = (
                f"- {item.pattern.normalized_content} "
                f"[support: {item.independent_support}; "
                f"contradictions: {item.independent_contradictions}]"
            )
            if size + 1 + len(line) > _MAX_CONTENT:
                break
            lines.append(line)
            cited.append(item.pattern.pattern_id)
            size += 1 + len(line)
        if not cited:
            raise LearningValidationError("no supported pattern fits the synthesis content bound")
        contradicted = [item for item in ordered if not item.independent_support]
        if contradicted:
            note = f"Excluded contradicted reports: {len(contradicted)}"
            if size + 1 + len(note) <= _MAX_CONTENT:
                lines.append(note)
        return SynthesisDraft(
            title=f"Synthesis: {supported[0].pattern.normalized_content[:400]}",
            content="\n".join(lines),
            cited_pattern_ids=tuple(cited),
        )


class SynthesisModel(ABC):
    """An application-supplied model that drafts text from bounded evidence."""

    @abstractmethod
    async def generate(self, request: SynthesisInput) -> SynthesisDraft:
        """Return a draft that cites the patterns it relies on."""


class ModelSynthesizer(PatternSynthesizer):
    """Delegate drafting to an injected model and enforce grounding and time bounds.

    Every cited pattern must be in the request and independently supported. The
    model's text is untrusted candidate content and is never activated without review.
    """

    def __init__(self, model: SynthesisModel, *, name: Identifier, timeout: float = 30.0) -> None:
        if not 0 < timeout <= 600:
            raise ValueError("synthesis timeout must be between 0 and 600 seconds")
        self.model = model
        self._name = name
        self.timeout = timeout

    @property
    def name(self) -> Identifier:
        """Return the configured synthesizer identifier."""
        return self._name

    async def synthesize(self, request: SynthesisInput) -> SynthesisDraft:
        """Generate and validate a draft within the configured timeout."""
        try:
            draft = await asyncio.wait_for(self.model.generate(request), self.timeout)
        except TimeoutError as exc:
            raise LearningValidationError("synthesis model timed out") from exc
        return SynthesisDraft.model_validate(draft.model_dump())


def validate_draft(request: SynthesisInput, draft: SynthesisDraft) -> tuple[PatternSupport, ...]:
    """Return the cited patterns, rejecting unknown, duplicate or unsupported citations."""
    available = {item.pattern.pattern_id: item for item in request.patterns}
    if len(set(draft.cited_pattern_ids)) != len(draft.cited_pattern_ids):
        raise LearningValidationError("synthesis draft cites a pattern more than once")
    cited = []
    for identifier in draft.cited_pattern_ids:
        item = available.get(identifier)
        if item is None:
            raise LearningValidationError("synthesis draft cites an unknown pattern")
        if not item.independent_support:
            raise LearningValidationError("synthesis draft cites an unsupported pattern")
        cited.append(item)
    return tuple(cited)


def cluster_patterns(
    patterns: Sequence[PatternGroup], similarities: Sequence[PatternSimilarity]
) -> tuple[tuple[PatternGroup, ...], ...]:
    """Group patterns connected by similarity suggestions within one scope and kind.

    Suggestions may come from ``PatternMiner.similar`` or a host-computed source such
    as RetrievalLoom similarity. Clusters larger than 50 patterns are rejected.
    """
    by_id = {pattern.pattern_id: pattern for pattern in patterns}
    if len(by_id) != len(patterns):
        raise LearningValidationError("clustering requires distinct patterns")
    parent = {identifier: identifier for identifier in by_id}

    def root(identifier: str) -> str:
        while parent[identifier] != identifier:
            parent[identifier] = parent[parent[identifier]]
            identifier = parent[identifier]
        return identifier

    for suggestion in similarities:
        left = by_id.get(suggestion.left_pattern_id)
        right = by_id.get(suggestion.right_pattern_id)
        if left is None or right is None:
            raise LearningValidationError("similarity suggestion references an unknown pattern")
        if left.scope != right.scope or left.kind != right.kind:
            raise LearningValidationError("similar patterns must share scope and kind")
        first, second = sorted((root(left.pattern_id), root(right.pattern_id)))
        parent[second] = first
    clusters: dict[str, list[PatternGroup]] = {}
    for identifier in sorted(by_id):
        clusters.setdefault(root(identifier), []).append(by_id[identifier])
    result = []
    for members in clusters.values():
        if len(members) < 2:
            continue
        if len(members) > _MAX_CLUSTER:
            raise LearningValidationError("similarity cluster exceeds 50 patterns")
        result.append(tuple(members))
    return tuple(sorted(result, key=lambda cluster: cluster[0].pattern_id))


__all__ = [
    "DeterministicSynthesizer",
    "ModelSynthesizer",
    "PatternSupport",
    "PatternSynthesizer",
    "SynthesisDraft",
    "SynthesisInput",
    "SynthesisModel",
    "cluster_patterns",
    "validate_draft",
]
