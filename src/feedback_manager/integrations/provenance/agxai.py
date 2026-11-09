"""Resolving feedback provenance from an ``agxai`` runtime.

This is the only module that reads ``agxai`` records. It translates what
``agxai`` captured into a `FeedbackProvenanceReference`; it never captures
provenance itself. ``agxai`` is framework-agnostic, so this provider works with
any agent framework ``agxai`` instruments, including custom ones built on
``agxai.FrameworkInstrumentor``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from agxai import (
    AttributionEngine,
    CapturePolicy,
    Execution,
    ExplanationEngine,
    ObservabilityProvider,
    PolicyProvider,
    ProvenanceStore,
    StoreFilter,
    XAIRuntime,
)

from feedback_manager.contracts.provenance import ProvenanceProvider
from feedback_manager.core.provenance import FeedbackProvenanceReference
from feedback_manager.errors import FeedbackConfigurationError

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from agxai import Decision, Evidence, Registry, XAIConfig, XAIPlugin

    from feedback_manager.core.context import ExecutionContext
    from feedback_manager.core.events import FeedbackEvent

_PAGE_SIZE = 100


def create_agxai_runtime(
    *,
    config: XAIConfig | None = None,
    registry: Registry | None = None,
    application_id: str = "application",
    tenant_id: str = "default",
    graph_id: str = "graph",
    plugins: Sequence[XAIPlugin] = (),
    custom_state_capture: Callable[[Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]]
    | None = None,
    store: ProvenanceStore | None = None,
    observability: ObservabilityProvider | None = None,
    attribution: AttributionEngine | None = None,
    explanation: ExplanationEngine | None = None,
    policy: PolicyProvider | None = None,
    capture_policy: CapturePolicy | None = None,
) -> XAIRuntime:
    """Build an ``agxai.XAIRuntime`` with any of its components replaced.

    Every ``agxai`` extension point can be supplied here: the runtime settings
    and identity, plugins, custom state capture, and each registered
    capability. A capability left as ``None`` keeps the provider already in
    ``registry``, or agxai's default. Instrument your graphs with the returned
    runtime and pass it to `AgxaiProvenanceProvider`.

    Args:
        config: ``agxai.XAIConfig`` capture, failure, and concurrency settings.
        registry: An ``agxai.Registry`` to share providers between runtimes.
        application_id: Default application identifier of recorded runs.
        tenant_id: Default tenant identifier of recorded runs.
        graph_id: Default graph identifier of recorded runs.
        plugins: ``agxai.XAIPlugin`` instances that receive semantic artifacts.
        custom_state_capture: The state capture function ``agxai`` requires
            for ``CaptureMode.CUSTOM``.
        store: The ``agxai.ProvenanceStore`` that records executions.
        observability: The ``agxai.ObservabilityProvider`` canonical events go to.
        attribution: The ``agxai.AttributionEngine`` that scores decisions.
        explanation: The ``agxai.ExplanationEngine`` that renders explanations.
        policy: The ``agxai.PolicyProvider`` that filters explanations by audience.
        capture_policy: The ``agxai.CapturePolicy`` that gates captured events.

    Returns:
        The configured runtime.

    Raises:
        FeedbackConfigurationError: If a component or plugin is not an instance
            of its ``agxai`` base class, or ``agxai`` rejects the configuration.
    """
    capabilities: tuple[tuple[str, type[Any], object | None], ...] = (
        ("store", ProvenanceStore, store),
        ("observability", ObservabilityProvider, observability),
        ("attribution", AttributionEngine, attribution),
        ("explanation", ExplanationEngine, explanation),
        ("policy", PolicyProvider, policy),
        ("capture_policy", CapturePolicy, capture_policy),
    )
    for name, capability, provider in capabilities:
        if provider is not None and not isinstance(provider, capability):
            raise FeedbackConfigurationError(
                f"{name} must be an agxai.{capability.__name__}, not {type(provider).__name__}"
            )
    try:
        runtime = XAIRuntime(
            config,
            registry,
            application_id=application_id,
            tenant_id=tenant_id,
            graph_id=graph_id,
            plugins=tuple(plugins),
            custom_state_capture=custom_state_capture,
        )
    except (TypeError, ValueError) as error:
        raise FeedbackConfigurationError(str(error)) from error
    for _, capability, provider in capabilities:
        if provider is not None:
            runtime.register(capability, provider)
    return runtime


class AgxaiProvenanceProvider(ProvenanceProvider):
    """Builds `FeedbackProvenanceReference` objects from an ``agxai.XAIRuntime``.

    Pass one to ``FeedbackManager(provenance_provider=...)``; call `resolve`
    yourself to resolve provenance for an existing event, for example after
    loading it from a store.

    Resolution follows the feedback's ``ExecutionContext.run_id``:

    - Submitted inside an instrumented call, without a ``run_id`` or with the
      active run's ID: the active run is used, including the latest decision
      and its evidence.
    - With the ``run_id`` of another run: that run's latest ``Execution`` is
      read from ``store``, or from the ``ProvenanceStore`` registered on the
      runtime when ``store`` is not given. Decisions and evidence are not
      stored by ``agxai``, so they are not part of the reference.

    Within the run, the reference points at the latest execution of the
    context's ``node_id``, the tool execution with its ``tool_call_id``, and
    the human interaction recorded for its ``interrupt_id``.

    Bring your own storage by passing any ``agxai.ProvenanceStore``, for example
    the store your own agxai runtime writes to, or a read replica of it. Like
    every agxai store, it must accept ``agxai.StoreFilter`` queries; verify a
    custom store with ``agxai.testing.ProvenanceStoreContract``.

    To replace any other ``agxai`` component (observability, attribution,
    explanation, policies, plugins, configuration), build the runtime with
    `create_agxai_runtime` or ``XAIRuntime.register``.

    Args:
        runtime: The agxai runtime whose active run and identity are used.
            Defaults to a new runtime with agxai's defaults; read it from
            `runtime` to instrument your graphs.
        store: Where past runs are read from. Defaults to the store registered
            on ``runtime``.

    Raises:
        FeedbackConfigurationError: If ``runtime`` is not an ``agxai.XAIRuntime``
            or ``store`` is not an ``agxai.ProvenanceStore``.
    """

    def __init__(
        self, runtime: XAIRuntime | None = None, *, store: ProvenanceStore | None = None
    ) -> None:
        if runtime is None:
            runtime = create_agxai_runtime()
        if not isinstance(runtime, XAIRuntime):
            raise FeedbackConfigurationError(
                f"runtime must be an agxai.XAIRuntime, not {type(runtime).__name__}"
            )
        if store is not None and not isinstance(store, ProvenanceStore):
            raise FeedbackConfigurationError(
                f"store must be an agxai.ProvenanceStore, not {type(store).__name__}"
            )
        self._runtime = runtime
        self._store = store

    @property
    def runtime(self) -> XAIRuntime:
        """The agxai runtime this provider reads from."""
        return self._runtime

    @property
    def store(self) -> ProvenanceStore | None:
        """The store past runs are read from, or ``None`` when none is available."""
        if self._store is not None:
            return self._store
        return self._runtime.registry.get(ProvenanceStore)

    async def resolve(self, feedback: FeedbackEvent) -> FeedbackProvenanceReference | None:
        """Return the provenance of ``feedback``, or ``None`` when no run matches."""
        context = feedback.execution_context
        requested = _run_id(context)
        run = self._runtime.current_run
        if run is not None and requested in (None, run.run_id):
            return _reference(run.execution, context, run.decisions, run.evidence)
        if requested is None:
            return None
        execution = await self._stored_execution(requested, context)
        if execution is None:
            return None
        return _reference(execution, context, (), ())

    async def _stored_execution(
        self, run_id: UUID, context: ExecutionContext | None
    ) -> Execution | None:
        store = self.store
        if store is None:
            return None
        application_id = (
            context.application_id
            if context is not None and context.application_id is not None
            else self._runtime.application_id
        )
        tenant_id = (
            context.tenant_id
            if context is not None and context.tenant_id is not None
            else self._runtime.tenant_id
        )
        latest: Execution | None = None
        offset = 0
        while True:
            page = [
                item
                async for item in store.query(
                    StoreFilter(
                        application_id=application_id,
                        tenant_id=tenant_id,
                        run_id=run_id,
                        item_type=Execution,
                        limit=_PAGE_SIZE,
                        offset=offset,
                    )
                )
            ]
            for item in page:
                if isinstance(item, Execution):
                    latest = item
            if len(page) < _PAGE_SIZE:
                return latest
            offset += _PAGE_SIZE


def _run_id(context: ExecutionContext | None) -> UUID | None:
    if context is None or context.run_id is None:
        return None
    try:
        return UUID(context.run_id)
    except ValueError:
        return None


def _reference(
    execution: Execution,
    context: ExecutionContext | None,
    decisions: Sequence[Decision],
    evidence: Sequence[Evidence],
) -> FeedbackProvenanceReference:
    node_id = None if context is None else context.node_id
    tool_call_id = None if context is None else context.tool_call_id
    interrupt_id = None if context is None else context.interrupt_id
    nodes = [node for node in execution.nodes if node_id is not None and node.node_id == node_id]
    tools = [
        tool
        for tool in execution.tools
        if tool_call_id is not None and tool.tool_call_id == tool_call_id
    ]
    interactions = [
        interaction
        for interaction in execution.human_interactions
        if interrupt_id is not None and interaction.request_reference == interrupt_id
    ]
    decision = decisions[-1] if decisions else None
    evidence_ids = (
        tuple(str(item) for item in decision.evidence_ids)
        if decision is not None
        else tuple(str(item.id) for item in evidence)
    )
    run_id = str(execution.context.run_id)
    status = execution.status.value
    return FeedbackProvenanceReference(
        run_id=run_id,
        execution_id=str(execution.id),
        summary=(
            f"agxai run {run_id} ({status}): {len(execution.nodes)} node "
            f"execution(s), {len(execution.tools)} tool execution(s)"
        ),
        node_execution_id=str(nodes[-1].id) if nodes else None,
        tool_execution_id=str(tools[-1].id) if tools else None,
        human_interaction_id=str(interactions[-1].id) if interactions else None,
        decision_id=None if decision is None else str(decision.id),
        evidence_ids=evidence_ids,
        metadata={
            "status": status,
            "graph_id": execution.context.graph_id,
            "thread_id": execution.context.thread_id,
            "continuation_of": (
                None if execution.continuation_of is None else str(execution.continuation_of)
            ),
            "node_count": len(execution.nodes),
            "tool_count": len(execution.tools),
            "human_interaction_count": len(execution.human_interactions),
        },
    )


__all__ = ["AgxaiProvenanceProvider", "create_agxai_runtime"]
