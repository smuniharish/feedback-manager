# Learning: signals, artifacts, and review

## Contents

- [Signals](#signals)
- [Artifacts](#artifacts)
- [Validation policy](#validation-policy)
- [Review lifecycle](#review-lifecycle)
- [Scopes](#scopes)
- [Correcting feedback](#correcting-feedback)
- [Manual learning without the background worker](#manual-learning-without-the-background-worker)

## Signals

`LearningSignalExtractor.extract(feedback, *, scope)` turns one
`FeedbackEvent` into a `LearningSignal`, or returns a `reason` when the feedback
has nothing to learn. Signal types: `correction`, `approval`, `rejection`,
`failure`, `success`, `improvement`, `constraint`.

Write the lesson into the payload under a key the extractor reads, such as
`correction` or `improvement`. The original feedback stays the authoritative
record; signals only point at it.

## Artifacts

A `LearnedArtifact` has an `artifact_type` (`knowledge`, `strategy`,
`constraint`, or `pattern`), `title`, `content`, `scope`, `evidence`,
`confidence`, `version`, `status`, `provenance`, `validation_history`, and
`supersedes`. Every change creates a new version; earlier versions remain.

Signals with the same lesson in the same scope aggregate into one artifact.
Replaying the same signal does not add duplicate evidence.

## Validation policy

`LearningEngine(repository, validation_policy=...)` takes a
`LearningValidationPolicy`. The default is
`DefaultLearningValidationPolicy(LearningValidationConfig())`:

| Setting | Default |
|---|---|
| `min_independent_evidence` | 2 |
| `min_confidence` | 0.7 |
| `max_contradictions` | 0 |
| `min_source_reliability` | 0.0 |
| `max_evidence_age_days` | `None` |
| `require_manual_review` | `True` |
| `minimum_by_type`, `minimum_by_visibility` | `{}` |

Evidence is independent when it comes from different executions. Raise the
minimums per artifact type or visibility for stricter publication; never lower
`require_manual_review` for content that reaches prompts.

## Review lifecycle

| From | Legal targets |
|---|---|
| `candidate` | `under_review`, `rejected` |
| `under_review` | `validated`, `candidate`, `rejected` |
| `validated` | `active`, `under_review`, `rejected` |
| `active` | `under_review`, `deprecated`, `superseded` |
| `deprecated` | `under_review`, `superseded` |
| `rejected`, `superseded` | none |

```python
await engine.request_review(scope, artifact_id)  # -> under_review
await engine.review(scope, artifact_id, reviewer=user_id, approved=True)  # -> validated
await engine.activate(scope, artifact_id)  # -> active
```

`review(..., approved=False, reason="...")` rejects. `review` raises
`LearningValidationError` when the artifact does not meet the policy, and
binds the decision to the reviewed content and policy version: if the content
changes, it returns to review. Only `active` artifacts are retrieved.

## Scopes

`ArtifactScope(tenant_id, namespace="default", visibility=..., agent_id=...,
agent_type=..., task_type=..., tool=..., workflow=...)`. Visibility is
`agent` (one agent), `agent_type` (every agent of a type), or `tenant`.

The background learner resolves a scope for each piece of feedback with
`learning_scope_resolver`: a `ScopeResolver` subclass or a function
`(FeedbackEvent) -> ArtifactScope | None`. The default,
`ContextScopeResolver`, uses the tenant from `for_tenant` or the execution
context, and the agent from `execution_context.agent_id`. Returning `None`
skips the feedback.

```python
def scope_for(feedback):
    tenant = feedback.execution_context.tenant_id
    if tenant is None:
        return None
    return ArtifactScope(tenant_id=tenant, agent_type="support-agent", visibility="agent_type")


manager = FeedbackManager(learning_engine=engine, learning_scope_resolver=scope_for)
```

## Correcting feedback

When feedback turns out to be wrong, call
`engine.invalidate_feedback(scope, feedback_id)`. It marks that evidence
invalid on every affected artifact, keeps its provenance, and returns the
changed artifacts; active artifacts that no longer meet the policy return to
review. Coordinate it with one learning writer per tenant and namespace.

## Manual learning without the background worker

```python
from feedback_manager.learning.signals import LearningSignalExtractor

manager = FeedbackManager(learning=False)
extractor = LearningSignalExtractor()
extracted = extractor.extract(event, scope=scope)
if extracted.signal is not None:
    result = await engine.process([extracted.signal])
    candidate = result.artifacts[0]
```

`process` writes each pattern independently; a failed batch may have already
written earlier candidates.
