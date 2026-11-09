"""Check a feedback-manager component against its public contract, without pytest.

Imports a component factory, picks the contract kit from
`feedback_manager.testing` that matches the component's base class, runs every
test of it against new components from the factory, and reports one line per
promise:

    python scripts/check_component.py myapp.storage:PostgresFeedbackStore
    python scripts/check_component.py myapp.storage:make_vector_store --json

The factory is a class or a callable, sync or async, that takes no arguments
and returns a new, empty component. It is called once per test. Run it from
the project root, or pass --path. Supported base classes: FeedbackStore,
ArtifactRepository, LearningJobStore, VectorStore, GraphStore, and
EmbeddingProvider.

Exit codes: 0 every promise holds, 1 a promise is broken, 2 the factory
cannot be imported or does not build a supported component.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import sys
import traceback
from importlib import import_module
from pathlib import Path
from typing import Any


def load_factory(spec: str) -> Any:
    module_name, separator, attribute = spec.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError(f"expected MODULE:FACTORY, got {spec!r}")
    target: Any = import_module(module_name)
    for part in attribute.split("."):
        target = getattr(target, part)
    if not callable(target):
        raise TypeError(f"{spec} is not callable")
    return target


def kits() -> list[tuple[type, type, str]]:
    from feedback_manager.contracts import FeedbackStore
    from feedback_manager.embeddings import EmbeddingProvider
    from feedback_manager.learning import LearningJobStore
    from feedback_manager.storage.artifacts import ArtifactRepository
    from feedback_manager.storage.graph import GraphStore
    from feedback_manager.storage.vector import VectorStore
    from feedback_manager.testing import (
        ArtifactRepositoryContract,
        EmbeddingProviderContract,
        FeedbackStoreContract,
        GraphStoreContract,
        LearningJobStoreContract,
        VectorStoreContract,
    )

    return [
        (FeedbackStore, FeedbackStoreContract, "make_store"),
        (ArtifactRepository, ArtifactRepositoryContract, "make_repository"),
        (LearningJobStore, LearningJobStoreContract, "make_job_store"),
        (VectorStore, VectorStoreContract, "make_vector_store"),
        (GraphStore, GraphStoreContract, "make_graph_store"),
        (EmbeddingProvider, EmbeddingProviderContract, "make_provider"),
    ]


async def build(factory: Any) -> Any:
    component = factory()
    if inspect.isawaitable(component):
        component = await component
    return component


def contract_for(factory: Any) -> type:
    sample = asyncio.run(build(factory))
    for base, kit, method in kits():
        if isinstance(sample, base):

            async def make(self: Any) -> Any:
                return await build(factory)

            return type(f"{kit.__name__}Check", (kit,), {method: make})
    supported = ", ".join(base.__name__ for base, _, _ in kits())
    raise TypeError(f"the factory returned {type(sample).__name__}, which is none of: {supported}")


def run_contract(contract: type) -> list[dict[str, Any]]:
    results = []
    for name in sorted(name for name in dir(contract) if name.startswith("test_")):
        try:
            getattr(contract(), name)()
        except AssertionError as error:
            results.append({"test": name, "passed": False, "error": str(error)})
        except Exception as error:  # report every broken promise, then the summary
            detail = "".join(traceback.format_exception_only(error)).strip()
            results.append({"test": name, "passed": False, "error": detail})
        else:
            results.append({"test": name, "passed": True, "error": None})
    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("factory", help="MODULE:FACTORY returning a new, empty component")
    parser.add_argument("--json", action="store_true", help="print one JSON object to stdout")
    parser.add_argument(
        "--path", type=Path, default=Path.cwd(), help="directory to import from (default: cwd)"
    )
    arguments = parser.parse_args()
    sys.path.insert(0, str(arguments.path.resolve()))
    try:
        contract = contract_for(load_factory(arguments.factory))
    except Exception as error:
        message = f"cannot load {arguments.factory}: {type(error).__name__}: {error}"
        if arguments.json:
            print(json.dumps({"ok": False, "contract": None, "error": message, "results": []}))
        else:
            print(message, file=sys.stderr)
        return 2
    kit = contract.__mro__[1].__name__
    results = run_contract(contract)
    failed = [result for result in results if not result["passed"]]
    if arguments.json:
        report = {"ok": not failed, "contract": kit, "error": None, "results": results}
        print(json.dumps(report, indent=2))
    else:
        print(f"Checking against {kit}")
        for result in results:
            line = f"{'PASS' if result['passed'] else 'FAIL'}  {result['test']}"
            print(line + (f"\n      {result['error']}" if result["error"] else ""))
        print(
            f"All {len(results)} promises hold."
            if not failed
            else f"{len(failed)} of {len(results)} promises broken."
        )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
