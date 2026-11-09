from uuid import uuid4

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader, Sum
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from feedback_manager.observability.hooks import ObservabilityEvent
from feedback_manager.observability.operations import OperationObserver
from feedback_manager.observability.otel import OpenTelemetryObservabilitySink


def test_telemetry_excludes_sensitive_contents_and_cardinality():
    exporter = InMemorySpanExporter()
    tracer = TracerProvider()
    tracer.add_span_processor(SimpleSpanProcessor(exporter))
    reader = InMemoryMetricReader()
    meter = MeterProvider(metric_readers=[reader])
    try:
        sink = OpenTelemetryObservabilitySink(tracer_provider=tracer, meter_provider=meter)
        assert OperationObserver.observe(sink, "test") is None
        event = ObservabilityEvent(
            name="feedback.failed",
            feedback_id=uuid4(),
            attributes={"error": "secret", "payload": "private", "source": "unbounded"},
        )
        sink.emit(event)
        sink.emit(ObservabilityEvent(name="arbitrary", feedback_id=uuid4()))
        spans = exporter.get_finished_spans()
        assert spans[0].status.status_code is StatusCode.ERROR
        attributes = spans[0].attributes
        assert attributes is not None
        assert dict(attributes) == {"feedback.event": "feedback.failed"}
        assert spans[1].name == "feedback.other"
        data = reader.get_metrics_data()
        assert data is not None
        metric = data.resource_metrics[0].scope_metrics[0].metrics[0]
        assert isinstance(metric.data, Sum)
        points = metric.data.data_points
        assert sum(point.value for point in points) == 2
        assert all(
            point.attributes == {"feedback.event": point.attributes["feedback.event"]}
            for point in points
            if point.attributes is not None
        )
        with_ids = OpenTelemetryObservabilitySink(
            tracer_provider=tracer, meter_provider=meter, include_identifiers=True
        )
        with_ids.emit(event)
        attributes = exporter.get_finished_spans()[-1].attributes
        assert attributes is not None
        assert attributes["feedback.id"] == str(event.feedback_id)
    finally:
        tracer.shutdown()
        meter.shutdown()


async def test_learning_operation_traces_and_safe_failure_timing():
    from feedback_manager import (
        ExecutionContext,
        FeedbackManager,
        FeedbackTarget,
        FeedbackTargetType,
    )
    from feedback_manager.embeddings import HashingEmbeddingProvider
    from feedback_manager.learning import ArtifactScope
    from feedback_manager.learning.engine import LearningEngine
    from feedback_manager.retrieval import ArtifactIndexer, HybridRetriever, RetrievalRequest
    from feedback_manager.storage.artifacts import InMemoryArtifactRepository
    from feedback_manager.storage.graph import InMemoryGraphStore
    from feedback_manager.storage.vector import InMemoryVectorStore
    from tests.learning.test_retrieval import active_artifact

    exporter = InMemorySpanExporter()
    tracer = TracerProvider()
    tracer.add_span_processor(SimpleSpanProcessor(exporter))
    reader = InMemoryMetricReader()
    meter = MeterProvider(metric_readers=[reader])
    sink = OpenTelemetryObservabilitySink(tracer_provider=tracer, meter_provider=meter)
    scope = ArtifactScope(tenant_id="tenant", agent_id="alice")
    repo, vectors, graph = InMemoryArtifactRepository(), InMemoryVectorStore(), InMemoryGraphStore()
    engine, provider = LearningEngine(repo, observer=sink), HashingEmbeddingProvider()
    try:
        async with FeedbackManager(learning_engine=engine, observability_sink=sink) as manager:
            await manager.submit(
                source="human",
                category="correction",
                target=FeedbackTarget(type=FeedbackTargetType.RUN, id="run"),
                execution_context=ExecutionContext(tenant_id="tenant", agent_id="alice"),
                payload={"correction": "private text"},
            )
            assert manager.background_learner is not None
            await manager.background_learner.drain()
        artifact = await active_artifact(repo, scope)
        await engine.request_review(scope, artifact.artifact_id)
        await engine.review(scope, artifact.artifact_id, reviewer="reviewer", approved=True)
        artifact = await engine.activate(scope, artifact.artifact_id)
        await ArtifactIndexer(repo, vectors, provider, graph=graph, observer=sink).index(
            scope, artifact.artifact_id
        )
        result = await HybridRetriever(
            repo, vectors, provider, graph=graph, observer=sink
        ).retrieve(RetrievalRequest(scope=scope, agent_id="alice", task="validate", graph_depth=1))
        assert result.artifacts == (artifact,)
        with (
            pytest.raises(RuntimeError, match="sensitive exception"),
            sink.observe("unbounded-secret"),
        ):
            raise RuntimeError("sensitive exception")
        spans = exporter.get_finished_spans()
        names = {span.name for span in spans}
        assert {
            "learning.job",
            "learning.batch",
            "learning.projection",
            "learning.retrieval",
            "embedding.documents",
            "embedding.query",
            "database.vector.read",
            "database.vector.write",
            "database.graph.read",
            "database.graph.write",
            "database.artifact.read",
            "database.artifact.write",
        } <= names
        assert spans[-1].name == "operation.other"
        assert spans[-1].status.status_code is StatusCode.ERROR
        assert not spans[-1].events
        assert "sensitive exception" not in repr(spans)
        data = reader.get_metrics_data()
        assert data is not None
        assert "feedback_manager.operation.duration" in repr(data)
        assert "unbounded-secret" not in repr(data)
    finally:
        tracer.shutdown()
        meter.shutdown()
