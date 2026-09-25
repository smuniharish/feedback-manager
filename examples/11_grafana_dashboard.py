"""Example 11 -- real Grafana dashboard over feedback-manager's real data.

``feedback-manager`` does not ship a custom observability/UI stack (Section
6 of the spec explicitly rules out "custom telemetry backend" / "custom
UI"). Grafana + PostgreSQL is a mature, ubiquitous combination for exactly
this job, so this example *provisions a real Grafana dashboard* (via
Grafana's own HTTP API -- no custom charting code) that queries the real
``feedback_events`` table used throughout the other examples, and adds a
:class:`~feedback_manager.observability.hooks.ObservabilitySink` that posts
Grafana annotations for key lifecycle transitions, so the dashboard also
shows *when* feedback was acknowledged/resolved/failed, not just counts.

Prerequisites (all real, started with Podman -- see ``docs/examples/
grafana-observability.md`` for the exact commands)::

    fm-postgres   PostgreSQL 16, already used by examples/postgres_feedback_store.py
    fm-grafana    Grafana 11, on the same "fm-net" Podman network as fm-postgres

Run with::

    uv run python examples/11_grafana_dashboard.py
"""

from __future__ import annotations

import asyncio
import os
import selectors
import sys
from typing import Any

import httpx

from feedback_manager import (
    FeedbackCategory,
    FeedbackManager,
    FeedbackSource,
    FeedbackTarget,
    FeedbackTargetType,
)
from feedback_manager.observability.hooks import ObservabilityEvent

GRAFANA_URL = os.environ.get("FEEDBACK_MANAGER_GRAFANA_URL", "http://192.168.65.189:3000")
GRAFANA_AUTH = (
    os.environ.get("FEEDBACK_MANAGER_GRAFANA_USER", "admin"),
    os.environ.get("FEEDBACK_MANAGER_GRAFANA_PASSWORD", "feedback"),
)
# Grafana reaches Postgres over the "fm-net" Podman network by container name.
POSTGRES_HOST_FROM_GRAFANA = "fm-postgres:5432"


class GrafanaAnnotationObservabilitySink:
    """A real :class:`ObservabilitySink` -- posts lifecycle events to Grafana.

    Implements the ``ObservabilitySink`` protocol (``emit(event) -> None``)
    by calling Grafana's ``/api/annotations`` endpoint synchronously via
    ``httpx``, so every acknowledge/resolve/fail shows up as a marker on the
    real dashboard's time-series panels -- not just as a log line.
    """

    def __init__(self, base_url: str, auth: tuple[str, str], tags: list[str] | None = None) -> None:
        self._client = httpx.Client(base_url=base_url, auth=auth, timeout=5.0)
        self._tags = tags or ["feedback-manager"]

    def emit(self, event: ObservabilityEvent) -> None:
        payload: dict[str, Any] = {
            "text": f"{event.name} ({event.feedback_id})",
            "tags": [*self._tags, event.name],
            "time": int(event.occurred_at.timestamp() * 1000),
        }
        response = self._client.post("/api/annotations", json=payload)
        response.raise_for_status()

    def close(self) -> None:
        self._client.close()


def _run(coro: Any) -> Any:
    if sys.platform == "win32":
        return asyncio.run(
            coro, loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())
        )
    return asyncio.run(coro)


def _grafana_request(method: str, path: str, **kwargs: Any) -> httpx.Response:
    response = httpx.request(
        method, f"{GRAFANA_URL}{path}", auth=GRAFANA_AUTH, timeout=15.0, **kwargs
    )
    response.raise_for_status()
    return response


def provision_datasource() -> int:
    """Create (or reuse) a real Grafana PostgreSQL datasource, via Grafana's own API."""
    existing = httpx.get(
        f"{GRAFANA_URL}/api/datasources/name/feedback-manager-postgres",
        auth=GRAFANA_AUTH,
        timeout=15.0,
    )
    if existing.status_code == 200:
        return int(existing.json()["id"])

    payload = {
        "name": "feedback-manager-postgres",
        "type": "postgres",
        "access": "proxy",
        "url": POSTGRES_HOST_FROM_GRAFANA,
        "database": "feedback_manager",
        "user": "feedback",
        "secureJsonData": {"password": "feedback"},
        "jsonData": {"sslmode": "disable", "postgresVersion": 1600, "timescaledb": False},
    }
    response = httpx.post(
        f"{GRAFANA_URL}/api/datasources", auth=GRAFANA_AUTH, json=payload, timeout=15.0
    )
    response.raise_for_status()
    datasource_id: int = response.json()["datasource"]["id"]
    return datasource_id


def _sql_panel(
    panel_id: int, title: str, sql: str, viz_type: str, grid: dict[str, int], uid: str
) -> dict[str, Any]:
    panel: dict[str, Any] = {
        "id": panel_id,
        "title": title,
        "type": viz_type,
        "gridPos": grid,
        "datasource": {"type": "postgres", "uid": uid},
        "targets": [
            {
                "rawSql": sql,
                "format": "table",
                "datasource": {"type": "postgres", "uid": uid},
                "refId": "A",
            }
        ],
    }
    if viz_type == "bargauge":
        # Without this, bargauge reduces every row to a single aggregated
        # value instead of drawing one bar per (label, count) row.
        panel["options"] = {
            "displayMode": "gradient",
            "orientation": "horizontal",
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": True},
            "showUnfilled": True,
        }
    return panel


# examples/10_full_matrix_feedback.py tags every event it submits with
# ``payload["matrix_probe"] = True``. That script exists purely to prove the
# pipeline *accepts* every (source, category, target type) combination -- it
# is a synthetic coverage check, not organic feedback. If the main dashboard
# mixed the two, "events by category"/"events by target type" would show a
# near-uniform ~104/~120 for every value (1560 matrix combinations / 15
# categories = 104 each; 1560 / 13 target types = 120 each) which looks
# suspicious but is exactly the expected count for an exhaustive cross
# product -- it just isn't a picture of real usage. So the dashboard has two
# separate rows: real/organic feedback (matrix probes excluded), and the
# matrix coverage check (matrix probes only), clearly labelled as such.
_EXCLUDE_MATRIX_PROBES = "WHERE NOT COALESCE((data->'payload'->>'matrix_probe')::boolean, false)"
_ONLY_MATRIX_PROBES = "WHERE COALESCE((data->'payload'->>'matrix_probe')::boolean, false)"


def provision_dashboard(datasource_uid: str) -> str:
    """Create a real Grafana dashboard with panels over the real feedback_events table."""
    dashboard = {
        "dashboard": {
            "id": None,
            "uid": "feedback-manager-overview",
            "title": "feedback-manager: real feedback events",
            "timezone": "browser",
            "schemaVersion": 39,
            "refresh": "5s",
            "panels": [
                {
                    "id": 100,
                    "title": "Real / organic feedback (matrix coverage probes excluded)",
                    "type": "row",
                    "gridPos": {"h": 1, "w": 24, "x": 0, "y": 0},
                },
                _sql_panel(
                    1,
                    "Total organic feedback events",
                    f"SELECT count(*) AS total FROM feedback_events {_EXCLUDE_MATRIX_PROBES};",
                    "stat",
                    {"h": 6, "w": 6, "x": 0, "y": 1},
                    datasource_uid,
                ),
                _sql_panel(
                    2,
                    "Organic events by source",
                    "SELECT data->>'source' AS source, count(*) AS events "
                    f"FROM feedback_events {_EXCLUDE_MATRIX_PROBES} GROUP BY 1 ORDER BY 2 DESC;",
                    "barchart",
                    {"h": 10, "w": 9, "x": 6, "y": 1},
                    datasource_uid,
                ),
                _sql_panel(
                    3,
                    "Organic events by status",
                    "SELECT status, count(*) AS events "
                    f"FROM feedback_events {_EXCLUDE_MATRIX_PROBES} GROUP BY 1 ORDER BY 2 DESC;",
                    "piechart",
                    {"h": 10, "w": 9, "x": 15, "y": 1},
                    datasource_uid,
                ),
                _sql_panel(
                    4,
                    "Organic events by category",
                    "SELECT data->>'category' AS category, count(*) AS events "
                    f"FROM feedback_events {_EXCLUDE_MATRIX_PROBES} GROUP BY 1 ORDER BY 2 DESC;",
                    "bargauge",
                    {"h": 10, "w": 12, "x": 0, "y": 11},
                    datasource_uid,
                ),
                _sql_panel(
                    5,
                    "Organic events by target type",
                    "SELECT data->'target'->>'type' AS target_type, count(*) AS events "
                    f"FROM feedback_events {_EXCLUDE_MATRIX_PROBES} GROUP BY 1 ORDER BY 2 DESC;",
                    "bargauge",
                    {"h": 10, "w": 12, "x": 12, "y": 11},
                    datasource_uid,
                ),
                _sql_panel(
                    6,
                    "Organic feedback events over time",
                    "SELECT created_at AS time, data->>'source' AS metric, count(*) OVER "
                    "(PARTITION BY data->>'source' ORDER BY created_at) AS running_total "
                    f"FROM feedback_events {_EXCLUDE_MATRIX_PROBES} ORDER BY created_at;",
                    "timeseries",
                    {"h": 10, "w": 24, "x": 0, "y": 21},
                    datasource_uid,
                ),
                {
                    "id": 101,
                    "title": (
                        "Combinatorial coverage check (examples/10_full_matrix_feedback.py -- "
                        "synthetic probes, not organic feedback)"
                    ),
                    "type": "row",
                    "gridPos": {"h": 1, "w": 24, "x": 0, "y": 31},
                },
                _sql_panel(
                    7,
                    "Total matrix probe events (expect 8 x 15 x 13 = 1560)",
                    f"SELECT count(*) AS total FROM feedback_events {_ONLY_MATRIX_PROBES};",
                    "stat",
                    {"h": 6, "w": 6, "x": 0, "y": 32},
                    datasource_uid,
                ),
                _sql_panel(
                    8,
                    "Matrix probes by source (expect 195 each = 15 x 13)",
                    "SELECT data->>'source' AS source, count(*) AS events "
                    f"FROM feedback_events {_ONLY_MATRIX_PROBES} GROUP BY 1 ORDER BY 2 DESC;",
                    "bargauge",
                    {"h": 10, "w": 9, "x": 6, "y": 32},
                    datasource_uid,
                ),
                _sql_panel(
                    9,
                    "Matrix probes by category (expect 104 each = 8 x 13)",
                    "SELECT data->>'category' AS category, count(*) AS events "
                    f"FROM feedback_events {_ONLY_MATRIX_PROBES} GROUP BY 1 ORDER BY 2 DESC;",
                    "bargauge",
                    {"h": 10, "w": 9, "x": 15, "y": 32},
                    datasource_uid,
                ),
                _sql_panel(
                    10,
                    "Matrix probes by target type (expect 120 each = 8 x 15)",
                    "SELECT data->'target'->>'type' AS target_type, count(*) AS events "
                    f"FROM feedback_events {_ONLY_MATRIX_PROBES} GROUP BY 1 ORDER BY 2 DESC;",
                    "bargauge",
                    {"h": 10, "w": 24, "x": 0, "y": 42},
                    datasource_uid,
                ),
            ],
        },
        "overwrite": True,
    }
    response = _grafana_request("POST", "/api/dashboards/db", json=dashboard)
    return str(response.json()["url"])


async def _generate_some_lifecycle_events(sink: GrafanaAnnotationObservabilitySink) -> None:
    """Submit and transition a few real events so the dashboard has fresh annotations."""
    dsn = os.environ["FEEDBACK_MANAGER_POSTGRES_DSN"]
    from postgres_feedback_store import PostgresFeedbackStore

    store = await PostgresFeedbackStore.connect(dsn)
    manager = FeedbackManager(store=store, observability_sink=sink)

    event = await manager.submit(
        source=FeedbackSource.EVALUATOR,
        category=FeedbackCategory.QUALITY,
        target=FeedbackTarget(type=FeedbackTargetType.GENERATION, id="grafana-demo"),
        payload={"comment": "annotated in Grafana"},
    )
    await manager.acknowledge(event.feedback_id)
    await manager.mark_handled(event.feedback_id)
    await manager.resolve(event.feedback_id, resolution={"resolved_via": "grafana-demo"})


def main() -> None:
    print(f"Grafana URL: {GRAFANA_URL}")
    datasource_id = provision_datasource()
    print(f"Provisioned/verified real Grafana PostgreSQL datasource, id={datasource_id}")

    datasource = _grafana_request("GET", f"/api/datasources/{datasource_id}").json()
    datasource_uid = datasource["uid"]

    health = _grafana_request("GET", f"/api/datasources/{datasource_id}/health").json()
    print(f"Datasource health check (real query against fm-postgres): {health}")

    dashboard_url = provision_dashboard(datasource_uid)
    print(f"Provisioned real dashboard: {GRAFANA_URL}{dashboard_url}")

    sink = GrafanaAnnotationObservabilitySink(GRAFANA_URL, GRAFANA_AUTH)
    try:
        if os.environ.get("FEEDBACK_MANAGER_POSTGRES_DSN"):
            _run(_generate_some_lifecycle_events(sink))
            print(
                "Submitted a real feedback event and posted its lifecycle as real Grafana annotations."
            )
        else:
            print("FEEDBACK_MANAGER_POSTGRES_DSN not set -- skipping the annotation demo.")
    finally:
        sink.close()


if __name__ == "__main__":
    main()
