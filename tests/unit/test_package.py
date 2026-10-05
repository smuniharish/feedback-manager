"""The public package surface."""

from __future__ import annotations

import importlib
import subprocess
import sys
from importlib.metadata import version

import pytest

import feedback_manager

PUBLIC_MODULES = [
    "feedback_manager",
    "feedback_manager.api",
    "feedback_manager.contracts",
    "feedback_manager.core",
    "feedback_manager.correlation",
    "feedback_manager.errors",
    "feedback_manager.handlers",
    "feedback_manager.integrations.langchain",
    "feedback_manager.integrations.langgraph",
    "feedback_manager.integrations.xai",
    "feedback_manager.observability",
    "feedback_manager.policies",
    "feedback_manager.routing",
    "feedback_manager.storage",
]


@pytest.mark.parametrize("module_name", PUBLIC_MODULES)
def test_every_exported_name_exists(module_name: str) -> None:
    module = importlib.import_module(module_name)

    for name in module.__all__:
        assert hasattr(module, name), f"{module_name}.{name}"


def test_version_matches_the_installed_distribution() -> None:
    assert feedback_manager.__version__ == version("feedback-manager")


def test_importing_the_package_does_not_load_langgraph() -> None:
    code = (
        "import sys, feedback_manager; "
        "print(sorted(m for m in ('langgraph', 'langgraph_xai', 'langchain_core') "
        "if m in sys.modules))"
    )

    # The interpreter and the snippet are both fixed by this test.
    completed = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=60
    )

    assert completed.stdout.strip() == "[]"
