"""Every downstream component can be supplied or configured by the application."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest
from agxai import (
    AttributionEngine,
    CaptureMode,
    CapturePolicy,
    DefaultCapturePolicy,
    DefaultPolicyProvider,
    ExplanationEngine,
    HybridAttribution,
    NoOpObservability,
    ObservabilityProvider,
    PolicyProvider,
    ProvenanceStore,
    Registry,
    StructuredExplanationEngine,
    XAIConfig,
    XAIPlugin,
    XAIRuntime,
)

from feedback_manager import FeedbackConfigurationError
from feedback_manager.integrations.provenance import (
    AgxaiProvenanceProvider,
    create_agxai_runtime,
)
from feedback_manager.learning import TenantScope
from feedback_manager.storage.graph import GraphNode
from tests.integration.test_xai_custom_store import AuditedStore

if TYPE_CHECKING:
    from pathlib import Path

    from agxai.plugins.base import SemanticArtifact

pytestmark = pytest.mark.integration

SCOPE = TenantScope(tenant_id="tenant")


class Observability(NoOpObservability):
    pass


class Attribution(HybridAttribution):
    pass


class Explanation(StructuredExplanationEngine):
    pass


class Policy(DefaultPolicyProvider):
    pass


class Capture(DefaultCapturePolicy):
    pass


class Plugin(XAIPlugin):
    async def record(self, artifact: SemanticArtifact) -> None:
        pass


class TestCreateAgxaiRuntime:
    def test_every_agxai_component_can_be_replaced(self) -> None:
        store, observability, attribution = AuditedStore(), Observability(), Attribution()
        explanation, policy, capture = Explanation(), Policy(), Capture()
        plugin = Plugin()
        config = XAIConfig(max_concurrency=4)

        runtime = create_agxai_runtime(
            config=config,
            application_id="app",
            tenant_id="tenant",
            graph_id="graph",
            plugins=[plugin],
            store=store,
            observability=observability,
            attribution=attribution,
            explanation=explanation,
            policy=policy,
            capture_policy=capture,
        )

        assert runtime.config is config
        assert (runtime.application_id, runtime.tenant_id, runtime.graph_id) == (
            "app",
            "tenant",
            "graph",
        )
        assert runtime.plugins.plugins == (plugin,)
        expected: dict[type[Any], object] = {
            ProvenanceStore: store,
            ObservabilityProvider: observability,
            AttributionEngine: attribution,
            ExplanationEngine: explanation,
            PolicyProvider: policy,
            CapturePolicy: capture,
        }
        for capability, provider in expected.items():
            assert runtime.registry.get(capability) is provider

    def test_unset_components_keep_the_registry_providers_or_agxai_defaults(self) -> None:
        shared = AuditedStore()
        registry = Registry()
        registry.register(ProvenanceStore, shared)

        runtime = create_agxai_runtime(registry=registry, policy=Policy())

        assert runtime.registry is registry
        assert runtime.registry.get(ProvenanceStore) is shared
        assert isinstance(runtime.registry.get(AttributionEngine), HybridAttribution)

    def test_custom_state_capture_is_forwarded(self) -> None:
        def capture(before: Any, after: Any) -> dict[str, Any]:
            return dict(after)

        runtime = create_agxai_runtime(
            config=XAIConfig(capture_state=CaptureMode.CUSTOM), custom_state_capture=capture
        )

        assert runtime.custom_state_capture is capture

    def test_configuration_agxai_rejects_is_a_configuration_error(self) -> None:
        with pytest.raises(FeedbackConfigurationError, match="custom_state_capture"):
            create_agxai_runtime(config=XAIConfig(capture_state=CaptureMode.CUSTOM))

    @pytest.mark.parametrize(
        "name",
        ["store", "observability", "attribution", "explanation", "policy", "capture_policy"],
    )
    def test_components_must_subclass_their_agxai_base(self, name: str) -> None:
        with pytest.raises(FeedbackConfigurationError, match=f"^{name} must be an agxai"):
            create_agxai_runtime(**{name: object()})  # type: ignore[arg-type]

    def test_plugins_must_subclass_xai_plugin(self) -> None:
        with pytest.raises(FeedbackConfigurationError, match="XAIPlugin"):
            create_agxai_runtime(plugins=[object()])  # type: ignore[list-item]

    def test_the_provider_builds_a_default_runtime_when_none_is_given(self) -> None:
        provider = AgxaiProvenanceProvider()

        assert isinstance(provider.runtime, XAIRuntime)
        assert provider.store is provider.runtime.registry.get(ProvenanceStore)

    def test_the_provider_exposes_the_runtime_it_was_given(self) -> None:
        runtime = create_agxai_runtime(store=AuditedStore())

        assert AgxaiProvenanceProvider(runtime).runtime is runtime


class TestLanceDBCustomization:
    async def test_connect_options_are_forwarded_to_lancedb(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pytest.importorskip("lancedb")
        from feedback_manager.storage.vector import lancedb as module

        seen: list[dict[str, Any]] = []
        connect = module.lancedb.connect_async

        async def recording_connect(uri: str, **options: Any) -> Any:
            seen.append(options)
            return await connect(uri, **options)

        monkeypatch.setattr(module.lancedb, "connect_async", recording_connect)
        options = {"read_consistency_interval": timedelta(0)}

        store = await module.LanceDBVectorStore.open(
            str(tmp_path), connect_options=options, max_search_candidates=7
        )

        assert seen == [options]
        assert store.uri == str(tmp_path)

    async def test_open_uses_an_existing_connection(self, tmp_path: Path) -> None:
        lancedb = pytest.importorskip("lancedb")
        from feedback_manager.storage.vector.lancedb import LanceDBVectorStore

        connection = await lancedb.connect_async(str(tmp_path))

        store = await LanceDBVectorStore.open(str(tmp_path), connection=connection)

        assert store._connection is connection

    def test_connect_options_cannot_be_combined_with_a_connection(self, tmp_path: Path) -> None:
        pytest.importorskip("lancedb")
        from feedback_manager.storage.vector.lancedb import LanceDBVectorStore

        with pytest.raises(ValueError, match="connect_options"):
            LanceDBVectorStore(str(tmp_path), connection=object(), connect_options={"api_key": "k"})  # type: ignore[arg-type]


class TestLadybugCustomization:
    async def test_an_application_owned_database_is_used_and_left_open(
        self, tmp_path: Path
    ) -> None:
        ladybug = pytest.importorskip("ladybug")
        from feedback_manager.storage.graph.ladybug import LadybugGraphStore

        database = ladybug.Database(str(tmp_path / "graph.lbug"))
        node = GraphNode(node_id="one", scope=SCOPE, node_type="Artifact")

        async with LadybugGraphStore(database=database) as store:
            assert store.database is database
            assert not store.read_only
            await store.upsert_nodes([node])

        assert not database.is_closed
        async with LadybugGraphStore(database=database) as reopened:
            assert list(await reopened.get_nodes(SCOPE, ["one"])) == [node]
        database.close()

    async def test_a_read_only_database_makes_the_store_read_only(self, tmp_path: Path) -> None:
        ladybug = pytest.importorskip("ladybug")
        from feedback_manager.storage.graph.ladybug import (
            LadybugGraphStore,
            ReadOnlyGraphStoreError,
        )

        path = str(tmp_path / "graph.lbug")
        LadybugGraphStore(path).close()
        database = ladybug.Database(path, read_only=True)
        store = LadybugGraphStore(database=database)

        with pytest.raises(ReadOnlyGraphStoreError):
            await store.upsert_nodes([GraphNode(node_id="x", scope=SCOPE, node_type="Artifact")])
        assert store.read_only
        await store.aclose()
        database.close()

    async def test_database_options_configure_the_opened_database(self, tmp_path: Path) -> None:
        pytest.importorskip("ladybug")
        from feedback_manager.storage.graph.ladybug import LadybugGraphStore

        size = 64 * 1024 * 1024
        async with LadybugGraphStore(
            tmp_path / "graph.lbug", database_options={"buffer_pool_size": size}
        ) as store:
            assert store.database.buffer_pool_size == size
        assert store.database.is_closed

    @pytest.mark.parametrize(
        ("arguments", "message"),
        [
            ({}, "exactly one"),
            ({"path": "a", "database": object()}, "exactly one"),
            ({"database": object(), "database_options": {"compression": False}}, "only when"),
            ({"path": "a", "database_options": {"read_only": True}}, "read_only argument"),
        ],
    )
    def test_invalid_combinations_are_rejected(
        self, arguments: dict[str, Any], message: str
    ) -> None:
        pytest.importorskip("ladybug")
        from feedback_manager.storage.graph.ladybug import LadybugGraphStore

        with pytest.raises(ValueError, match=message):
            LadybugGraphStore(**arguments)
