"""Every extension point is an abstract base class; functions are adapted explicitly."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

import feedback_manager
from feedback_manager import (
    FeedbackConfigurationError,
    FeedbackEvent,
    FeedbackManager,
    FeedbackTarget,
)
from feedback_manager.contracts import FeedbackCorrelator, FeedbackSubscriber, FunctionSubscriber
from feedback_manager.integrations import LearnedArtifactRetriever
from feedback_manager.learning import (
    ArtifactScope,
    ContextScopeResolver,
    FunctionScopeResolver,
    ScopeResolver,
    ScopeVisibility,
)
from feedback_manager.learning.worker import as_scope_resolver
from feedback_manager.observability import ObservabilitySink, OperationObserver

PACKAGE = Path(feedback_manager.__file__).parent


@pytest.fixture
async def event(manager: FeedbackManager, target: FeedbackTarget) -> FeedbackEvent:
    return await manager.submit(source="human", category="rating", target=target, payload={})


def _imported_typing_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in {"typing", "typing_extensions"}:
            names.update(alias.name for alias in node.names)
    return names


@pytest.mark.parametrize("path", sorted(PACKAGE.rglob("*.py")), ids=lambda p: p.name)
def test_the_package_defines_no_typing_protocols(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    assert not {"Protocol", "runtime_checkable"} & _imported_typing_names(tree), path


@pytest.mark.parametrize(
    "contract",
    [
        FeedbackCorrelator,
        FeedbackSubscriber,
        LearnedArtifactRetriever,
        ObservabilitySink,
        OperationObserver,
        ScopeResolver,
    ],
)
def test_extension_points_are_abstract_base_classes(contract: type) -> None:
    assert inspect.isabstract(contract)
    with pytest.raises(TypeError):
        contract()


async def test_function_subscriber_forwards_events(event: FeedbackEvent) -> None:
    received: list[FeedbackEvent] = []

    async def record(feedback: FeedbackEvent) -> None:
        received.append(feedback)

    subscriber = FunctionSubscriber(record)
    await subscriber(event)

    assert received == [event]
    assert repr(subscriber).startswith("FunctionSubscriber(")


def test_subscribe_rejects_non_callables() -> None:
    manager = FeedbackManager(learning=False)
    with pytest.raises(FeedbackConfigurationError, match="FeedbackSubscriber"):
        manager.subscribe(42)  # type: ignore[arg-type]


def _tenant_scope(feedback: FeedbackEvent) -> ArtifactScope | None:
    return ArtifactScope(tenant_id="acme", visibility=ScopeVisibility.TENANT)


async def test_function_scope_resolver_forwards_feedback(event: FeedbackEvent) -> None:
    resolver = FunctionScopeResolver(_tenant_scope)

    scope = resolver(event)

    assert scope is not None
    assert scope.tenant_id == "acme"
    assert repr(resolver).startswith("FunctionScopeResolver(")


def test_as_scope_resolver_keeps_resolvers_and_wraps_functions() -> None:
    resolver = ContextScopeResolver(default_tenant="acme")

    assert as_scope_resolver(resolver) is resolver
    assert isinstance(as_scope_resolver(_tenant_scope), FunctionScopeResolver)
    with pytest.raises(TypeError, match="ScopeResolver"):
        as_scope_resolver("acme")  # type: ignore[arg-type]


def test_the_manager_accepts_plain_scope_functions() -> None:
    manager = FeedbackManager(learning_scope_resolver=_tenant_scope)

    assert manager.background_learner is not None
    assert isinstance(manager.background_learner.scope_resolver, FunctionScopeResolver)


def test_the_manager_rejects_non_callable_scope_resolvers() -> None:
    with pytest.raises(FeedbackConfigurationError, match="learning_scope_resolver"):
        FeedbackManager(learning_scope_resolver="acme")  # type: ignore[arg-type]
