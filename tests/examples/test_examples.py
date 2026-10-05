"""The self-contained examples run successfully and print what their docs show."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Any

import pytest

from tests.examples.conftest import EXAMPLES, ROOT

pytestmark = pytest.mark.examples

# Warnings are errors, except a deprecation that langsmith (a LangChain
# dependency) still triggers on Python 3.14.
STRICT_WARNINGS = [
    "-W",
    "error",
    "-W",
    "ignore:'asyncio.iscoroutinefunction' is deprecated:DeprecationWarning",
]
SELF_CONTAINED = {
    "01_human_correction.py": "Feedback about thread support-7: 1 event(s)",
    "02_hitl_approval.py": "Graph resumed and finished",
    "03_tool_failure.py": "Recorded tool/timeout feedback about tool_call 'call-weather-1'",
    "04_generation_interruption.py": "gen-101: interruption",
    "05_evaluator_feedback.py": "Routed to human review: ['gen-2']",
    "06_provenance.py": "Post-run feedback provenance",
    "09_override_defaults.py": "lifecycle_policy:   assign a reviewer before resolving",
    "10_full_matrix_feedback.py": "Distinct combinations stored: 1560 of 1560",
    "sqlite_feedback_store.py": "After reopening: resolved, resolution={'reviewed': True}",
}


@pytest.mark.parametrize(("script", "expected"), SELF_CONTAINED.items())
def test_example_runs(script: str, expected: str) -> None:
    if script.startswith("10_"):
        pytest.importorskip("psycopg", reason="needs the examples dependency group")
    environment = {
        key: value for key, value in os.environ.items() if key != "FEEDBACK_MANAGER_POSTGRES_DSN"
    }

    # Runs this repository's own example with the current interpreter.
    completed = subprocess.run(  # noqa: S603
        [sys.executable, *STRICT_WARNINGS, str(EXAMPLES / script)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
        check=False,
        cwd=ROOT,
        env=environment,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert expected in completed.stdout


def test_streamlit_app_submits_and_reviews_feedback() -> None:
    pytest.importorskip("streamlit", reason="needs the examples dependency group")
    from streamlit.testing.v1 import AppTest

    def review(app: AppTest) -> Any:
        (box,) = (box for box in app.selectbox if box.label == "Feedback")
        return box

    def statuses(app: AppTest) -> dict[str, str]:
        feed = app.dataframe[0].value
        return dict(zip((row["comment"] for row in feed["payload"]), feed["status"], strict=True))

    script = str(EXAMPLES / "streamlit_feedback_ui.py")
    reviewer = AppTest.from_file(script, default_timeout=60).run()
    assert not reviewer.exception

    for comment in ("first", "second"):
        reviewer.text_area[0].set_value(comment)
        reviewer.button[0].click().run()
    assert reviewer.success[0].value.startswith("Recorded ")
    # Two submissions that look alike stay separate choices; pick the older one.
    assert len(review(reviewer).options) == 2
    review(reviewer).select_index(1).run()

    # Another session adds feedback before the reviewer acts: the choice holds.
    other = AppTest.from_file(script, default_timeout=60).run()
    other.text_area[0].set_value("third")
    other.button[0].click().run()
    reviewer.button[1].click().run()  # Acknowledge
    assert not reviewer.exception
    assert statuses(reviewer) == {
        "first": "acknowledged",
        "second": "received",
        "third": "received",
    }

    # Another session closes the reviewer's choice first: nothing else is touched.
    review(other).set_value(review(reviewer).value).run()
    other.button[4].click().run()  # Reject
    reviewer.button[4].click().run()  # Reject
    assert reviewer.warning[0].value.startswith("Choose the feedback to review first")
    assert statuses(reviewer) == {"first": "rejected", "second": "received", "third": "received"}
