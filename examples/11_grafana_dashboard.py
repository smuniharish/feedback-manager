"""Chart feedback in Grafana, and annotate dashboards with feedback lifecycle events.

feedback-manager ships no dashboards of its own; its data is ordinary SQL. This
example uses Grafana's HTTP API to provision a PostgreSQL data source and a
dashboard over the table `PostgresFeedbackStore` writes, then submits feedback
through `GrafanaAnnotationSink`, an `ObservabilitySink` that posts lifecycle
events as Grafana annotations from a background thread, so the feedback path
never waits on Grafana.

Start PostgreSQL and Grafana (see ``examples/compose.yaml``), then run:

    FEEDBACK_MANAGER_POSTGRES_DSN=postgresql://feedback:feedback@localhost:5432/feedback \\
        uv run python examples/11_grafana_dashboard.py

Grafana defaults to http://localhost:3000 with user ``admin`` and password
``feedback``; override with ``FEEDBACK_MANAGER_GRAFANA_URL``,
``FEEDBACK_MANAGER_GRAFANA_USER``, and ``FEEDBACK_MANAGER_GRAFANA_PASSWORD``.
"""

import logging
import os
import queue
import sys
import threading
from typing import Any, NamedTuple

import httpx
from postgres_feedback_store import DSN_VARIABLE, PostgresFeedbackStore, run

from feedback_manager import (
    FeedbackCategory,
    FeedbackManager,
    FeedbackSource,
    FeedbackTarget,
    FeedbackTargetType,
)
from feedback_manager.observability import FEEDBACK_RECEIVED, ObservabilityEvent

GRAFANA_URL = os.environ.get("FEEDBACK_MANAGER_GRAFANA_URL", "http://localhost:3000")
GRAFANA_AUTH = (
    os.environ.get("FEEDBACK_MANAGER_GRAFANA_USER", "admin"),
    os.environ.get("FEEDBACK_MANAGER_GRAFANA_PASSWORD", "feedback"),
)
DATASOURCE = {
    "name": "feedback-manager",
    "type": "grafana-postgresql-datasource",
    "access": "proxy",
    # How Grafana reaches PostgreSQL: by service name inside the compose network.
    "url": os.environ.get("FEEDBACK_MANAGER_GRAFANA_POSTGRES_HOST", "postgres:5432"),
    "user": "feedback",
    "jsonData": {"database": "feedback", "sslmode": "disable"},
    "secureJsonData": {"password": "feedback"},
}


class Panel(NamedTuple):
    """One dashboard panel: a SQL query, how to draw it, and where."""

    title: str
    kind: str
    sql: str
    height: int
    width: int
    x: int
    y: int


PANELS = [
    Panel("Feedback events", "stat", "SELECT count(*) AS events FROM feedback_events", 8, 6, 0, 0),
    Panel(
        "By source",
        "barchart",
        "SELECT source, count(*) AS events FROM feedback_events"
        " GROUP BY source ORDER BY events DESC",
        height=8,
        width=9,
        x=6,
        y=0,
    ),
    Panel(
        "By status",
        "piechart",
        "SELECT status, count(*) AS events FROM feedback_events GROUP BY status",
        height=8,
        width=9,
        x=15,
        y=0,
    ),
    Panel(
        "By category",
        "bargauge",
        "SELECT category, count(*) AS events FROM feedback_events"
        " GROUP BY category ORDER BY events DESC",
        height=14,
        width=12,
        x=0,
        y=8,
    ),
    Panel(
        "By target type",
        "bargauge",
        "SELECT target_type, count(*) AS events FROM feedback_events"
        " GROUP BY target_type ORDER BY events DESC",
        height=14,
        width=12,
        x=12,
        y=8,
    ),
    Panel(
        "Feedback over time",
        "timeseries",
        "SELECT $__timeGroupAlias(created_at, '1m'), count(*) AS events FROM feedback_events"
        " WHERE $__timeFilter(created_at) GROUP BY 1 ORDER BY 1",
        height=9,
        width=24,
        x=0,
        y=22,
    ),
]
# Rows, not a single value reduced from all rows: one bar or slice per group.
EVERY_ROW = {"calcs": ["lastNotNull"], "fields": "", "values": True}
PANEL_OPTIONS: dict[str, dict[str, Any]] = {
    "bargauge": {
        "displayMode": "gradient",
        "orientation": "horizontal",
        "reduceOptions": EVERY_ROW,
    },
    "piechart": {
        "pieType": "donut",
        "displayLabels": [],
        "legend": {
            "displayMode": "table",
            "placement": "right",
            "showLegend": True,
            "values": ["value"],
        },
        "reduceOptions": EVERY_ROW,
    },
}
FIELD_CONFIG: dict[str, dict[str, Any]] = {
    "timeseries": {
        "defaults": {"min": 0, "custom": {"drawStyle": "bars", "fillOpacity": 80}},
        "overrides": [],
    },
}

logger = logging.getLogger("examples.grafana")


class GrafanaAnnotationSink:
    """Posts lifecycle events to Grafana as annotations, from a background thread.

    `emit` only enqueues the event, so a slow or unreachable Grafana never
    delays feedback processing. Delivery failures are logged and dropped.
    """

    def __init__(self, base_url: str, auth: tuple[str, str]) -> None:
        self._client = httpx.Client(base_url=base_url, auth=auth, timeout=5.0)
        self._queue: queue.Queue[ObservabilityEvent | None] = queue.Queue()
        self._worker = threading.Thread(target=self._deliver, name="grafana-annotations")
        self._worker.start()
        self.posted = 0

    def emit(self, event: ObservabilityEvent) -> None:
        if event.name != FEEDBACK_RECEIVED:
            self._queue.put(event)

    def close(self) -> None:
        """Deliver the queued annotations, then stop the worker."""
        self._queue.put(None)
        self._worker.join()
        self._client.close()

    def _deliver(self) -> None:
        while (event := self._queue.get()) is not None:
            category = event.attributes["category"]
            annotation = {
                "time": int(event.occurred_at.timestamp() * 1000),
                "tags": ["feedback-manager", event.name],
                "text": f"{event.name}: {category} feedback {event.feedback_id}",
            }
            try:
                self._client.post("/api/annotations", json=annotation).raise_for_status()
                self.posted += 1
            except httpx.HTTPError:
                logger.exception("could not post annotation for %s", event.feedback_id)


def grafana(method: str, path: str, **kwargs: Any) -> httpx.Response:
    response = httpx.request(
        method, f"{GRAFANA_URL}{path}", auth=GRAFANA_AUTH, timeout=15, **kwargs
    )
    response.raise_for_status()
    return response


def provision_datasource() -> str:
    """Create the PostgreSQL data source unless it exists, and return its UID."""
    existing = httpx.get(
        f"{GRAFANA_URL}/api/datasources/name/{DATASOURCE['name']}", auth=GRAFANA_AUTH, timeout=15
    )
    if existing.status_code == httpx.codes.OK:
        return str(existing.json()["uid"])
    return str(grafana("POST", "/api/datasources", json=DATASOURCE).json()["datasource"]["uid"])


def provision_dashboard(datasource_uid: str) -> str:
    """Create or replace the dashboard, and return its URL path."""
    source = {"type": DATASOURCE["type"], "uid": datasource_uid}
    panels = []
    for panel_id, spec in enumerate(PANELS, start=1):
        target = {
            "refId": "A",
            "datasource": source,
            "rawSql": spec.sql,
            "editorMode": "code",
            "format": "time_series" if spec.kind == "timeseries" else "table",
        }
        panel: dict[str, Any] = {
            "id": panel_id,
            "title": spec.title,
            "type": spec.kind,
            "datasource": source,
            "gridPos": {"h": spec.height, "w": spec.width, "x": spec.x, "y": spec.y},
            "targets": [target],
        }
        if spec.kind in PANEL_OPTIONS:
            panel["options"] = PANEL_OPTIONS[spec.kind]
        if spec.kind in FIELD_CONFIG:
            panel["fieldConfig"] = FIELD_CONFIG[spec.kind]
        panels.append(panel)
    dashboard = {
        "dashboard": {
            "uid": "feedback-manager",
            "title": "feedback-manager",
            "refresh": "10s",
            "time": {"from": "now-6h", "to": "now"},
            "panels": panels,
            "annotations": {
                "list": [
                    {
                        "name": "Feedback lifecycle",
                        "datasource": {"type": "grafana", "uid": "-- Grafana --"},
                        "enable": True,
                        "iconColor": "purple",
                        "target": {"type": "tags", "tags": ["feedback-manager"]},
                    }
                ]
            },
        },
        "overwrite": True,
    }
    return str(grafana("POST", "/api/dashboards/db", json=dashboard).json()["url"])


async def record_lifecycle(dsn: str, sink: GrafanaAnnotationSink) -> None:
    """Submit feedback and take it through its lifecycle, annotating each step."""
    async with await PostgresFeedbackStore.open(dsn) as store:
        manager = FeedbackManager(store=store, observability_sink=sink)
        feedback = await manager.submit(
            source=FeedbackSource.EVALUATOR,
            category=FeedbackCategory.QUALITY,
            target=FeedbackTarget(type=FeedbackTargetType.GENERATION, id="dashboard-demo"),
            payload={"score": 0.4, "critique": "Misses the refund deadline."},
        )
        await manager.acknowledge(feedback.feedback_id)
        await manager.mark_handled(feedback.feedback_id)
        await manager.resolve(feedback.feedback_id, resolution={"fixed_in": "prompt-v12"})


def main() -> None:
    dsn = os.environ.get(DSN_VARIABLE)
    if not dsn:
        sys.exit(f"Set {DSN_VARIABLE} to the PostgreSQL connection string first.")
    datasource_uid = provision_datasource()
    health = grafana("GET", f"/api/datasources/uid/{datasource_uid}/health").json()
    print(f"Data source: {health['status']} ({health['message']})")
    print(f"Dashboard: {GRAFANA_URL}{provision_dashboard(datasource_uid)}")

    sink = GrafanaAnnotationSink(GRAFANA_URL, GRAFANA_AUTH)
    try:
        run(record_lifecycle(dsn, sink))
    finally:
        sink.close()
    print(f"Posted {sink.posted} lifecycle annotation(s)")


if __name__ == "__main__":
    main()
