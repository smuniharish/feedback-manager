"""Generous performance budgets that catch order-of-magnitude regressions."""

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.benchmark


def _overhead_script():
    path = Path(__file__).parents[2] / "scripts" / "benchmark_overhead.py"
    spec = importlib.util.spec_from_file_location("benchmark_overhead", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_overhead_script_reports_bounded_latency_for_every_scenario(capsys) -> None:
    script = _overhead_script()

    results = {result.name: result for result in await script.main(runs=30, warmup=5, lessons=5)}

    expected = {"submit", "submit+learning", "lifecycle", "retrieve:structured"}
    if importlib.util.find_spec("lancedb") is not None:
        expected.add("retrieve:hybrid")
    assert set(results) == expected
    for result in results.values():
        assert 0 < result.p50_ms <= result.p95_ms <= result.p99_ms
        assert result.p95_ms < 250
        assert result.peak_kib > 0
    assert "p95 ms" in capsys.readouterr().out
