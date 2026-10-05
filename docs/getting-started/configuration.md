# Configuration

`FeedbackManager` is configured entirely through its constructor. Every
argument is optional and keyword-only, and each one replaces a single default
with your implementation of a small, typed contract.

| Argument | Contract | Default | Learn more |
|---|---|---|---|
| `store` | [`FeedbackStore`](../api/contracts.md#feedback_manager.contracts.FeedbackStore) | `InMemoryFeedbackStore()` | [Storage](../concepts/storage.md) |
| `router` | [`FeedbackRouter`](../api/contracts.md#feedback_manager.contracts.FeedbackRouter) | `DefaultFeedbackRouter()`, which routes nothing | [Routing](../concepts/routing.md) |
| `correlator` | [`FeedbackCorrelator`](../api/contracts.md#feedback_manager.contracts.FeedbackCorrelator) | `DefaultFeedbackCorrelator()`: run, then thread, then checkpoint | [Correlation](../concepts/correlation.md) |
| `xai_runtime` | `langgraph_xai.XAIRuntime` | None: no provenance | [Provenance](../concepts/provenance.md) |
| `lifecycle_policy` | [`FeedbackLifecyclePolicy`](../api/contracts.md#feedback_manager.contracts.FeedbackLifecyclePolicy) | None: every legal move is allowed | [Lifecycle](../concepts/lifecycle.md) |
| `redaction_policy` | [`FeedbackRedactionPolicy`](../api/contracts.md#feedback_manager.contracts.FeedbackRedactionPolicy) | None: nothing is redacted | [Security](../operations/security.md) |
| `failure_policy` | [`FailurePolicy`](../api/policies.md#feedback_manager.policies.FailurePolicy) | Best-effort for every stage | [Failure isolation](../how-to/failure-isolation.md) |
| `observability_sink` | [`ObservabilitySink`](../api/observability.md#feedback_manager.observability.ObservabilitySink) | `LoggingObservabilitySink()` | [Observability](../concepts/observability.md) |

A wrong type fails fast: passing, say, a router where a store is expected
raises `FeedbackConfigurationError` from the constructor.

## A fully configured manager

```python
from langgraph_xai import XAIRuntime

from feedback_manager import FeedbackEvent, FeedbackLifecycleError, FeedbackManager, FeedbackStatus
from feedback_manager.contracts import FeedbackLifecyclePolicy, FeedbackRedactionPolicy
from feedback_manager.handlers import AuditFeedbackHandler
from feedback_manager.observability import LoggingObservabilitySink
from feedback_manager.policies import FailureMode, FailurePolicy, FeedbackStage
from feedback_manager.routing import DefaultFeedbackRouter, RoutingRule, by_category
from feedback_manager.storage import InMemoryFeedbackStore


class DropEmail(FeedbackRedactionPolicy):
    def redact(self, feedback: FeedbackEvent) -> FeedbackEvent:
        payload = {key: value for key, value in feedback.payload.items() if key != "email"}
        return feedback.model_copy(update={"payload": payload})


class ReviewerResolves(FeedbackLifecyclePolicy):
    def authorize_transition(self, feedback: FeedbackEvent, target: FeedbackStatus) -> None:
        if target is FeedbackStatus.RESOLVED and "reviewer" not in feedback.metadata:
            raise FeedbackLifecycleError("assign a reviewer before resolving")


manager = FeedbackManager(
    store=InMemoryFeedbackStore(),  # (1)!
    router=DefaultFeedbackRouter(
        [RoutingRule(by_category("correction"), [AuditFeedbackHandler()], name="audit")]
    ),
    xai_runtime=XAIRuntime(application_id="support-bot"),
    lifecycle_policy=ReviewerResolves(),
    redaction_policy=DropEmail(),
    failure_policy=FailurePolicy(modes={FeedbackStage.HANDLER: FailureMode.BLOCKING}),
    observability_sink=LoggingObservabilitySink("myapp.feedback"),
)
```

1.  Replace it with a durable store in production, such as the
    [PostgreSQL store](../examples/production.md#postgresql-store).

[Example 09](../examples/production.md#replace-every-default) replaces every
default and prints each one taking effect.

## Several managers

Managers share no state, so you can run one per tenant, per application area,
or per test in the same process. Feedback submitted to one manager is invisible
to the others unless they share a store.

## Logging

feedback-manager writes structured log records through the standard
[`logging`](https://docs.python.org/3/library/logging.html) module and never
configures logging itself. Configure handlers and levels as you would for any
library:

```python
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
```

Submitting and acknowledging feedback then logs:

```text
INFO feedback_manager.observability event='feedback.received' feedback_id='ef14d60f-a542-40c0-a9ce-2c398696a96f' occurred_at='2026-10-04T17:53:18.163558+00:00' source='human' category='rating' target_type='generation' status='received'
INFO feedback_manager.observability event='feedback.acknowledged' feedback_id='ef14d60f-a542-40c0-a9ce-2c398696a96f' occurred_at='2026-10-04T17:53:18.164140+00:00' source='human' category='rating' target_type='generation' status='acknowledged'
```

| Logger | What it logs |
|---|---|
| `feedback_manager.observability` | One record per [observability event](../concepts/observability.md), from the default sink. `feedback.failed` is a `WARNING`; the rest are `INFO`. |
| `feedback_manager.policies.failure` | A `WARNING` with the traceback when a best-effort stage fails. |
| `feedback_manager.manager` | A `WARNING` when the observability sink itself fails. |
| `feedback_manager.integrations.langchain` | A `WARNING` when a tool failure could not be recorded. |
| `feedback_manager.audit` | One `INFO` record per event handled by `AuditFeedbackHandler`. |

Payloads are never logged by the package.
