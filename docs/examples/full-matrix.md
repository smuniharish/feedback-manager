# Example: every (source, category, target type) combination, for real

Source file: `examples/10_full_matrix_feedback.py`

The [Streamlit example](streamlit-feedback-ui.md) proves a human can submit
feedback of *every* `FeedbackSource` through the real UI. That is one
representative event per source -- it does not prove the pipeline actually
accepts every value of every axis feedback-manager defines. This example
closes that gap by submitting one real `FeedbackEvent` for *every*
combination of:

- `FeedbackSource` -- 8 well-known values
- `FeedbackCategory` -- 15 well-known values
- `FeedbackTargetType` -- 13 well-known values

8 x 15 x 13 = **1560 real submissions**, each a genuine `manager.submit()`
call against the real PostgreSQL-backed store used throughout the other
examples (falls back to `InMemoryFeedbackStore` if
`FEEDBACK_MANAGER_POSTGRES_DSN` is unset), submitted with bounded
concurrency (`asyncio.Semaphore(25)`), then read back from the database to
prove every value of every axis actually round-tripped.

!!! warning "This is a coverage probe, not organic feedback"
    Every event this script submits carries `payload={"matrix_probe": True}`
    specifically so it can be told apart from real application feedback.
    The [Grafana dashboard](grafana-observability.md) uses that flag to keep
    its "real / organic feedback" panels honest -- see that page for why
    this matters and how the two are kept separate.

## Real run, real database

```console
$ uv run python examples/10_full_matrix_feedback.py
Backend: PostgreSQL
Matrix: 8 sources x 15 categories x 13 target types = 1560 combinations

Submitted 1560 real feedback events in 27.14s
Distinct sources observed:      8 / 8
Distinct categories observed:    15 / 15
Distinct target types observed:  13 / 13

Real events re-read back from the store: 1560 (expected 1560)
Distinct (source, category, target_type) triples persisted: 1560
Every combination round-tripped through the real pipeline exactly once.
```

Verifying directly against PostgreSQL (bypassing the package entirely):

```console
$ podman exec fm-postgres psql -U feedback -d feedback_manager -c \
    "SELECT count(*) FILTER (WHERE data->'payload'->>'matrix_probe' = 'true') AS matrix_probes, \
            count(DISTINCT data->>'source') AS distinct_sources, \
            count(DISTINCT data->>'category') AS distinct_categories, \
            count(DISTINCT data->'target'->>'type') AS distinct_target_types \
     FROM feedback_events;"
 matrix_probes | distinct_sources | distinct_categories | distinct_target_types
---------------+-------------------+----------------------+------------------------
          1560 |                 8 |                   15 |                     13
```
