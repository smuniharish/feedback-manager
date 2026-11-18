import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.concurrency

SCRIPT = Path(__file__).parents[2] / "scripts" / "stress_learning.py"


def test_workers_in_separate_processes_recover_a_crashed_job_without_duplicates(tmp_path):
    completed = subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(SCRIPT),
            *("--events", "12", "--workers", "3", "--topics", "3", "--lease", "1"),
            *("--crash", "--timeout", "120", "--directory", str(tmp_path)),
        ],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    summary = json.loads(completed.stdout)
    assert summary["ok"]
    assert summary["worker_exit_codes"]["crashing-worker"] == 137
    assert summary["reclaimed_jobs"] >= 1
    assert summary["artifacts"] == 3
    assert summary["dead_lettered"] == 0
