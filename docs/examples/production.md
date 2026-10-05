# Production

Patterns for running feedback-manager on real infrastructure: a database-backed
store, every extension point configured, dashboards, and a review UI.

## Start PostgreSQL and Grafana

`examples/compose.yaml` runs PostgreSQL 18 and Grafana 13 with local
development credentials:

```bash
uv sync --group examples
docker compose -f examples/compose.yaml up -d     # or: podman compose ...
export FEEDBACK_MANAGER_POSTGRES_DSN="postgresql://feedback:feedback@localhost:5432/feedback"
```

Grafana is then at <http://localhost:3000> (user `admin`, password `feedback`).

??? note "examples/compose.yaml"

    ```yaml
    --8<-- "examples/compose.yaml"
    ```

!!! tip "Container engines without localhost forwarding"
    Some container setups, such as a Podman machine on Windows, do not forward
    `localhost` to the containers. Use the machine's IP address in
    `FEEDBACK_MANAGER_POSTGRES_DSN` and `FEEDBACK_MANAGER_GRAFANA_URL` instead.

## PostgreSQL store

`PostgresFeedbackStore` is a production-ready `FeedbackStore`. Copy it into
your application and adapt the schema to your conventions.

- One connection pool shared by every operation (`psycopg_pool`).
- Idempotent creation with `INSERT ... ON CONFLICT (idempotency_key) DO NOTHING`.
- Compare-and-set transitions in a single `UPDATE ... WHERE status = <expected>`.
- Typed, indexed columns for every query filter, next to the full event as `JSONB`.
- Filtering, ordering, and limits done by the database.

??? example "examples/postgres_feedback_store.py"

    ```python
    --8<-- "examples/postgres_feedback_store.py"
    ```

Its self-check runs against `FEEDBACK_MANAGER_POSTGRES_DSN`:

```bash
uv run python examples/postgres_feedback_store.py
```

```text title="Output"
Stored 92f740e2-9f9c-490a-a8bc-4f8f50af6cfd (received)
Idempotent retry returned the same event: True
Resolved: resolved, resolution={'reviewed': True}
Latest self-check events: ['received', 'resolved']
```

psycopg's async mode needs a selector event loop, which is not the default on
Windows; the module's `run()` helper starts one.

## Replace every default

Every constructor argument of `FeedbackManager` swapped for a custom
implementation, each one printed taking effect: a store that counts writes, a
router and handler, a correlator, a `langgraph-xai` runtime, a lifecycle
policy that requires a reviewer, a redaction policy that masks emails, a
blocking routing failure, and an observability sink.

??? example "examples/09_override_defaults.py"

    ```python
    --8<-- "examples/09_override_defaults.py"
    ```

```text title="Output"
store:              1 write(s)
router + handler:   notified 1 time(s)
correlator:         correlation_id=conv-12
xai_runtime:        langgraph-xai run 2337d808-f7f5-4374-81fa-c518aff506db (completed): 1 node execution(s), 0 tool execution(s)
redaction_policy:   payload={'comment': 'Great answer', 'email': '***'}
lifecycle_policy:   assign a reviewer before resolving
failure_policy:     routing stage failed: LookupError: routing table unavailable (feedback_id=62c7fb0b-2755-4a33-a6a3-7abd2fb096e1)
observability_sink: ['feedback.received', 'feedback.routed', 'feedback.acknowledged', 'feedback.handled', 'feedback.received', 'feedback.failed']
```

## Every combination

Submits one event for each of the 8 sources, 15 categories, and 13 target
types, 1,560 submissions run concurrently, and checks that every combination
was stored. With `FEEDBACK_MANAGER_POSTGRES_DSN` set, it writes to a separate
`feedback_matrix` table, so these synthetic probes never mix with real
feedback.

??? example "examples/10_full_matrix_feedback.py"

    ```python
    --8<-- "examples/10_full_matrix_feedback.py"
    ```

```text title="Output"
Store: in-memory
Submitted 1560 events in 0.08s
Distinct sources: 8 of 8
Distinct categories: 15 of 15
Distinct target types: 13 of 13
Distinct combinations stored: 1560 of 1560
```

## Grafana dashboard

feedback-manager ships no dashboards: its data is ordinary SQL. This example
provisions a PostgreSQL data source and a dashboard through Grafana's HTTP API,
then records feedback through `GrafanaAnnotationSink`, an observability sink
that posts lifecycle events as Grafana annotations from a background thread,
so the feedback path never waits on Grafana.

```bash
uv run python examples/12_organic_scenarios_postgres.py   # realistic data, below
uv run python examples/11_grafana_dashboard.py
```

```text title="Output"
Data source: OK (Database Connection OK)
Dashboard: http://localhost:3000/d/feedback-manager/feedback-manager
Posted 3 lifecycle annotation(s)
```

[![The feedback-manager dashboard in Grafana](../assets/screenshots/grafana-dashboard.png)](../assets/screenshots/grafana-dashboard.png)

The dashed markers in "Feedback over time" are the lifecycle annotations.
Override the Grafana address and credentials with `FEEDBACK_MANAGER_GRAFANA_URL`,
`FEEDBACK_MANAGER_GRAFANA_USER`, and `FEEDBACK_MANAGER_GRAFANA_PASSWORD`.

??? example "examples/11_grafana_dashboard.py"

    ```python
    --8<-- "examples/11_grafana_dashboard.py"
    ```

## Realistic data

Seventeen scenarios from an agent application, each a single `submit` taken as
far through the lifecycle as the situation would go. Together they cover every
category and target type, which gives the dashboard varied, organic data.

```text title="Output"
agent       approval           tool_call    -> rejected
human       rejection          tool_call    -> resolved
tool        timeout            tool_result  -> resolved
tool        failure            tool_call    -> acknowledged
evaluator   uncertainty        message      -> acknowledged
evaluator   validation         state        -> resolved
agent       request_for_human  run          -> acknowledged
human       cancellation       thread       -> cancelled
system      partial_result     task         -> acknowledged
system      comment            checkpoint   -> received
application comment            application  -> received
human       correction         message      -> resolved
external    rating             agent        -> received
system      completion         graph        -> received
human       approval           node         -> resolved
generation  interruption       generation   -> acknowledged
evaluator   quality            generation   -> expired
Categories covered: 15 of 15
Target types covered: 13 of 13
```

??? example "examples/12_organic_scenarios_postgres.py"

    ```python
    --8<-- "examples/12_organic_scenarios_postgres.py"
    ```

## Streamlit UI

feedback-manager is a library, not a UI. This Streamlit app shows how little
code a feedback screen needs: a form to submit feedback, lifecycle buttons, and
a filtered feed. It keeps feedback in memory, or in PostgreSQL when
`FEEDBACK_MANAGER_POSTGRES_DSN` is set.

```bash
uv run streamlit run examples/streamlit_feedback_ui.py
```

[![The Streamlit feedback UI](../assets/screenshots/streamlit-feedback-ui.png)](../assets/screenshots/streamlit-feedback-ui.png)

??? example "examples/streamlit_feedback_ui.py"

    ```python
    --8<-- "examples/streamlit_feedback_ui.py"
    ```
