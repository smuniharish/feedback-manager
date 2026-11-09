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
    "feedback_manager.integrations",
    "feedback_manager.integrations.frameworks.custom",
    "feedback_manager.integrations.frameworks.langchain",
    "feedback_manager.integrations.provenance",
    "feedback_manager.learning",
    "feedback_manager.observability",
    "feedback_manager.policies",
    "feedback_manager.retrieval",
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


def _run(code: str) -> subprocess.CompletedProcess[str]:
    # The interpreter and the snippet are both fixed by these tests.
    return subprocess.run(  # noqa: S603
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=60
    )


def test_importing_the_core_does_not_load_any_agent_framework() -> None:
    code = (
        "import sys, feedback_manager, feedback_manager.integrations, "
        "feedback_manager.integrations.frameworks.custom, feedback_manager.learning, "
        "feedback_manager.retrieval; "
        "print(sorted(m for m in ('agxai', 'langgraph', 'langchain_core') if m in sys.modules))"
    )

    assert _run(code).stdout.strip() == "[]"


def test_the_langchain_adapter_explains_how_to_install_its_extra() -> None:
    # Blocks the framework imports, as if the [langchain] extra were not installed.
    code = (
        "import sys\n"
        "sys.modules['langchain_core'] = None\n"
        "sys.modules['langgraph'] = None\n"
        "import feedback_manager.integrations\n"
        "try:\n"
        "    import feedback_manager.integrations.frameworks.langchain\n"
        "except ImportError as error:\n"
        "    print(error)\n"
    )

    assert 'pip install "feedback-manager[langchain]"' in _run(code).stdout
