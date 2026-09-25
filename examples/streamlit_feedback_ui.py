"""A real, working feedback-capture UI built with Streamlit.

``feedback-manager`` is deliberately not a UI product (Section 0 of the
spec explicitly rules out "a UI product" / "a hosted service"). This file
demonstrates -- for developers embedding the package into *their* own
application -- how little glue code a real human-feedback capture surface
needs on top of :class:`~feedback_manager.api.manager.FeedbackManager`.
It is not part of the installable package; it lives in ``examples/`` as a
runnable reference.

Run it with::

    uv run streamlit run examples/streamlit_feedback_ui.py

By default it uses :class:`~feedback_manager.storage.memory.InMemoryFeedbackStore`
(so it runs with zero external services). Set ``FEEDBACK_MANAGER_POSTGRES_DSN``
to point it at a real PostgreSQL instance (see ``examples/postgres_feedback_store.py``)
to verify that feedback submitted through the UI is really persisted, not
just held in the Streamlit process's memory.
"""

from __future__ import annotations

import asyncio
import os
import selectors
import sys
from typing import Any
from uuid import UUID

import streamlit as st

from feedback_manager import (
    FeedbackCategory,
    FeedbackManager,
    FeedbackQuery,
    FeedbackSource,
    FeedbackStatus,
    FeedbackTarget,
    FeedbackTargetType,
)


def _run(coro: Any) -> Any:
    """Run an async ``feedback_manager`` call from Streamlit's sync script.

    Streamlit reruns this whole module top-to-bottom on every interaction,
    single-threaded, with no event loop already running -- so a fresh
    ``asyncio.run()`` per call is the simplest correct bridge. On Windows,
    ``psycopg``'s async mode additionally requires a selector-based loop
    (the default ``ProactorEventLoop`` does not support it).
    """
    if sys.platform == "win32":
        return asyncio.run(
            coro, loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())
        )
    return asyncio.run(coro)


@st.cache_resource
def get_manager() -> FeedbackManager:
    """Build (once per Streamlit session) the ``FeedbackManager`` the whole app shares."""
    dsn = os.environ.get("FEEDBACK_MANAGER_POSTGRES_DSN")
    if dsn:
        from postgres_feedback_store import PostgresFeedbackStore

        store = _run(PostgresFeedbackStore.connect(dsn))
        return FeedbackManager(store=store)
    return FeedbackManager()


def _target_options() -> list[str]:
    return [
        FeedbackTargetType.GENERATION,
        FeedbackTargetType.TOOL_CALL,
        FeedbackTargetType.NODE,
        FeedbackTargetType.AGENT,
        FeedbackTargetType.GRAPH,
        FeedbackTargetType.RUN,
    ]


def _category_options() -> list[str]:
    return [
        FeedbackCategory.RATING,
        FeedbackCategory.APPROVAL,
        FeedbackCategory.REJECTION,
        FeedbackCategory.CORRECTION,
        FeedbackCategory.COMMENT,
        FeedbackCategory.FAILURE,
        FeedbackCategory.QUALITY,
        FeedbackCategory.REQUEST_FOR_HUMAN,
    ]


def _source_options() -> list[str]:
    return [
        FeedbackSource.HUMAN,
        FeedbackSource.AGENT,
        FeedbackSource.EVALUATOR,
        FeedbackSource.APPLICATION,
        FeedbackSource.TOOL,
    ]


def render_submit_form(manager: FeedbackManager) -> None:
    st.subheader("Submit feedback")
    with st.form("submit_feedback", clear_on_submit=True):
        col1, col2, col3 = st.columns(3)
        source = col1.selectbox("Source", _source_options())
        category = col2.selectbox("Category", _category_options())
        target_type = col3.selectbox("Target type", _target_options())
        target_id = st.text_input("Target id", value="agent-run-1")
        rating = st.slider("Rating", min_value=1, max_value=5, value=4)
        comment = st.text_area("Comment", placeholder="What happened, and was it correct?")
        submitted = st.form_submit_button("Submit feedback", type="primary")

        if submitted:
            event = _run(
                manager.submit(
                    source=source,
                    category=category,
                    target=FeedbackTarget(type=target_type, id=target_id),
                    payload={"rating": rating, "comment": comment},
                )
            )
            st.success(f"Feedback {event.feedback_id} submitted -- status={event.status}")


def render_lifecycle_actions(manager: FeedbackManager) -> None:
    st.subheader("Lifecycle actions")
    events = _run(manager.list())
    if not events:
        st.info("No feedback submitted yet.")
        return

    labels = {
        f"{event.feedback_id} [{event.status}] {event.category} on {event.target.id}": event.feedback_id
        for event in events
    }
    selection = st.selectbox("Feedback event", list(labels.keys()))
    feedback_id: UUID = labels[selection]

    col1, col2, col3, col4 = st.columns(4)
    if col1.button("Acknowledge"):
        _run(manager.acknowledge(feedback_id))
        st.rerun()
    if col2.button("Mark handled"):
        _run(manager.mark_handled(feedback_id))
        st.rerun()
    if col3.button("Resolve"):
        _run(manager.resolve(feedback_id, resolution={"resolved_via": "streamlit-ui"}))
        st.rerun()
    if col4.button("Reject"):
        _run(manager.reject(feedback_id, reason="rejected via streamlit-ui"))
        st.rerun()


def render_feed(manager: FeedbackManager) -> None:
    st.subheader("Feedback feed")
    status_filter = st.selectbox(
        "Filter by status", ["(all)", *[status.value for status in FeedbackStatus]]
    )
    query = FeedbackQuery(
        status=None if status_filter == "(all)" else FeedbackStatus(status_filter)
    )
    events = _run(manager.query(query))

    if not events:
        st.info("Nothing matches this filter yet.")
        return

    rows = [
        {
            "feedback_id": str(event.feedback_id),
            "status": event.status.value,
            "source": str(event.source),
            "category": str(event.category),
            "target": f"{event.target.type}:{event.target.id}",
            "payload": event.payload,
            "created_at": event.created_at.isoformat(),
        }
        for event in events
    ]
    st.dataframe(rows, use_container_width=True)


def main() -> None:
    st.set_page_config(page_title="feedback-manager -- live feedback capture", layout="wide")
    st.title("feedback-manager: live feedback capture")
    st.caption(
        "Real FeedbackManager instance, backed by "
        + (
            "PostgreSQL (FEEDBACK_MANAGER_POSTGRES_DSN set)"
            if os.environ.get("FEEDBACK_MANAGER_POSTGRES_DSN")
            else "InMemoryFeedbackStore (set FEEDBACK_MANAGER_POSTGRES_DSN for real persistence)"
        )
    )
    manager = get_manager()

    render_submit_form(manager)
    st.divider()
    render_lifecycle_actions(manager)
    st.divider()
    render_feed(manager)


main()
