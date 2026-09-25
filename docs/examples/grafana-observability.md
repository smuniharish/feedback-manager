# Example: real Grafana observability over real feedback

Source files: `examples/11_grafana_dashboard.py`, `examples/12_organic_scenarios_postgres.py`

`feedback-manager` does not ship a custom telemetry backend or a custom UI
(Section 6 of the design explicitly rules those out). Grafana + PostgreSQL is
a mature, ubiquitous combination for exactly this job, so this example
**provisions a real Grafana dashboard via Grafana's own HTTP API** -- no
custom charting code -- that queries the real `feedback_events` table used
throughout the other examples, and adds an `ObservabilitySink` (`feedback_manager.observability.hooks`)
implementation that posts real Grafana annotations for key lifecycle
transitions (acknowledge/handle/resolve), so the dashboard shows *when*
feedback moved through its lifecycle, not just counts.

## Setup (all real, via Podman)

```console
$ podman network create fm-net
$ podman network connect fm-net fm-postgres
$ podman run -d --name fm-grafana --network fm-net -p 3000:3000 \
    -e GF_SECURITY_ADMIN_USER=admin -e GF_SECURITY_ADMIN_PASSWORD=feedback \
    grafana/grafana:11.4.0
```

`fm-net` gives Grafana's provisioned PostgreSQL datasource DNS access to
`fm-postgres:5432` by container name, instead of a raw IP.

```console
$ uv run python examples/11_grafana_dashboard.py
Grafana URL: http://192.168.65.189:3000
Provisioned/verified real Grafana PostgreSQL datasource, id=1
Datasource health check (real query against fm-postgres): {'message': 'Database Connection OK', 'status': 'OK'}
Provisioned real dashboard: http://192.168.65.189:3000/d/feedback-manager-overview/...
Submitted a real feedback event and posted its lifecycle as real Grafana annotations.
```

## Why the dashboard has two rows, not one

Once [example 10](full-matrix.md)'s 1560 synthetic combinatorial probes and
the handful of events from the other examples all landed in the same
`feedback_events` table, a single "events by category" panel became
misleading: every category showed a near-identical ~104 count (1560 / 15
categories), and every target type showed ~120 (1560 / 13 target types) --
mathematically correct for an exhaustive cross product, but not a picture of
*real* application usage, and easy to mistake for "everything is captured"
when most categories/target types had in fact never been exercised
organically.

So the dashboard is split into two clearly labelled rows:

- **"Real / organic feedback (matrix coverage probes excluded)"** -- filters
  out anything with `payload->>'matrix_probe'`, so it reflects genuine
  application usage only.
- **"Combinatorial coverage check (synthetic probes, not organic
  feedback)"** -- the exhaustive matrix from example 10, explicitly labelled
  as a coverage test, not real traffic.

`examples/12_organic_scenarios_postgres.py` exists to make the first row
meaningful: it submits 16 further genuine, distinct scenarios (an agent
requesting approval before a risky tool call and a human rejecting it, a
tool timing out then failing, an evaluator flagging low confidence and a
validation failure, a human cancelling a thread, a checkpoint replay audit
note, an interrupted generation, and more) against the real store, each
through the real `FeedbackManager` API (several taken through
`acknowledge -> mark_handled -> resolve`). Combined with the other examples
that also write to Postgres, every one of the 15 categories, all 13 target
types, and all 8 sources now has real, organic, non-uniform counts:

```console
$ uv run python examples/12_organic_scenarios_postgres.py
Submitted 16 genuinely distinct real feedback events to PostgreSQL:
  - source=agent       category=approval           target_type=tool_call   target_id=delete_production_table
  - source=human       category=rejection          target_type=tool_call   target_id=delete_production_table
  - source=tool        category=timeout            target_type=tool_result target_id=fetch_weather-call-1
  ...
Distinct categories captured this run: 14
Distinct target types captured this run: 13
```

```console
$ podman exec fm-postgres psql -U feedback -d feedback_manager -c \
    "SELECT count(*) AS organic_total, count(DISTINCT data->>'category') AS distinct_categories, \
            count(DISTINCT data->'target'->>'type') AS distinct_target_types, \
            count(DISTINCT data->>'source') AS distinct_sources \
     FROM feedback_events WHERE data->'payload'->>'matrix_probe' IS NULL;"
 organic_total | distinct_categories | distinct_target_types | distinct_sources
---------------+---------------------+------------------------+-------------------
            44 |                  15 |                     13 |                 8
```

## Screenshots (real, live dashboard)

Real, organic feedback -- every source, category, and target type now has a
genuine, non-uniform count (not the misleading near-uniform ~104/~120 the
combined view showed before the split):

![Grafana dashboard, real/organic feedback row, showing distinct non-uniform counts per source, category, and target type](../assets/screenshots/grafana-organic-vs-matrix.png)

The combinatorial coverage row, for comparison -- uniform 195/104/120 counts
are the *expected*, correct result of an exhaustive cross product, clearly
labelled as a synthetic probe rather than real traffic:

![Grafana dashboard, combinatorial coverage row, showing uniform 195/104/120 counts matching 8x15x13](../assets/screenshots/grafana-matrix-coverage.png)

## Real Grafana annotations from the observability sink

`GrafanaAnnotationObservabilitySink` implements the same
`ObservabilitySink` protocol as any other observability hook -- it just
happens to call Grafana's `/api/annotations` endpoint instead of a log
line or a metrics client:

```console
$ curl -u admin:feedback http://192.168.65.189:3000/api/annotations?limit=5
[{"id": 10, "text": "feedback.resolved (642cf1f4-...)", "tags": ["feedback-manager", "feedback.resolved"], ...}]
```

These annotations show up as markers directly on the dashboard's
time-series panel, letting you correlate *when* feedback was
acknowledged/handled/resolved with the surrounding event volume.
