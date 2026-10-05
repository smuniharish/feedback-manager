"""Shared setup for the example tests."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    import pytest
    from pytest_asyncio.plugin import LoopFactory

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples"
sys.path.insert(0, str(EXAMPLES))


def pytest_asyncio_loop_factories(
    config: pytest.Config, item: pytest.Item
) -> Mapping[str, LoopFactory]:
    """Run the example tests on a selector event loop.

    psycopg's async mode needs one, and it is not Windows' default. On Linux
    and macOS it is the default loop anyway.
    """
    return {"selector": asyncio.SelectorEventLoop}
