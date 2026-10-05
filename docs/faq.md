# FAQ

??? question "How is this different from LangSmith feedback or tracing?"
    LangSmith's feedback API attaches scores and comments to traced runs inside
    LangSmith. feedback-manager is a library in your application that keeps
    feedback as records in your own store, with a lifecycle, routing to your
    handlers, and integrations that capture failures and human-in-the-loop
    decisions. They work well together: a subscriber can forward feedback to
    LangSmith, and the `run_id` in each event's execution context links the two.

??? question "Do I need LangGraph or LangChain?"
    The core works in any asyncio application: submit, transition, query, route,
    and subscribe know nothing about frameworks. The integrations, such as the
    callback handler and the human-in-the-loop bridge, are for LangChain and
    LangGraph applications.

??? question "Do I need langgraph-xai?"
    It is installed as a dependency, but provenance is optional. Feedback only
    gets provenance when you pass an `xai_runtime` to `FeedbackManager`.

??? question "Is there a synchronous API?"
    No: storage and delivery are asynchronous. From synchronous code, call
    `asyncio.run`, or run an event loop in a background thread and submit work
    to it with `asyncio.run_coroutine_threadsafe`, as the
    [Streamlit example](examples/production.md#streamlit-ui) does.

??? question "Can several processes share feedback?"
    Yes, through a shared durable store such as the
    [PostgreSQL store](examples/production.md#postgresql-store): idempotency and
    lifecycle transitions are enforced by the database. Subscribers and streams
    deliver changes within one process only.

??? question "Can feedback be edited after it is submitted?"
    No. Events are immutable; only their status and resolution change through
    the lifecycle. To correct feedback, close it, for example with `reject` and a
    `reason`, and submit a new event.

??? question "How do I delete feedback?"
    Deletion is a data retention concern of your store: delete or archive
    closed feedback with your database's tooling. Use
    [expiry](how-to/expire-feedback.md) to close feedback that was never
    handled.

??? question "Does feedback-manager call a language model or send data anywhere?"
    No. It has no network clients of its own. Data leaves your process only
    through the stores, handlers, subscribers, and sinks you configure.

??? question "How do I send feedback to Slack, a ticketing system, or OpenTelemetry?"
    For new feedback, write a [handler](how-to/routing.md) and route to it. For
    every change, use a [subscriber](concepts/delivery.md). For metrics and
    traces, write an [observability sink](concepts/observability.md).

??? question "Which Python versions are supported?"
    Python 3.12, 3.13, and 3.14, on Linux, macOS, and Windows.

??? question "How stable is the API?"
    feedback-manager follows semantic versioning. While it is in `0.x`, minor
    releases may contain breaking changes; every one is listed in the
    [changelog](development/changelog.md).
