"""A feedback capture and review UI built with Streamlit.

feedback-manager is a library, not a UI. This app shows how little code a
feedback screen needs on top of `FeedbackManager`: a form to submit feedback,
lifecycle buttons, and a filtered feed. It keeps feedback in memory, or in
PostgreSQL when ``FEEDBACK_MANAGER_POSTGRES_DSN`` is set.

Run with:

    uv run streamlit run examples/streamlit_feedback_ui.py
"""

import asyncio
import os
import sys
import threading
from collections.abc import Coroutine
from typing import Any

import streamlit as st

from feedback_manager import (
    FeedbackCategory,
    FeedbackEvent,
    FeedbackLifecycleError,
    FeedbackManager,
    FeedbackQuery,
    FeedbackSource,
    FeedbackStatus,
    FeedbackTarget,
    FeedbackTargetType,
)
from feedback_manager.core import TERMINAL_STATUSES

DSN_VARIABLE = "FEEDBACK_MANAGER_POSTGRES_DSN"
OPEN_STATUSES = tuple(status for status in FeedbackStatus if status not in TERMINAL_STATUSES)


def event_label(event: FeedbackEvent) -> str:
    """How one event is listed; the ID prefix tells apart events that look alike."""
    return (
        f"{event.category} on {event.target.type} {event.target.id} "
        f"[{event.status}] {str(event.feedback_id)[:8]}"
    )


@st.cache_resource
def event_loop() -> asyncio.AbstractEventLoop:
    """One event loop for the whole app, so connection pools stay on a single loop."""
    loop = asyncio.SelectorEventLoop() if sys.platform == "win32" else asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, name="feedback-loop", daemon=True).start()
    return loop


def run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    """Run a coroutine on the app's event loop from Streamlit's script thread."""
    return asyncio.run_coroutine_threadsafe(coroutine, event_loop()).result()


@st.cache_resource
def feedback_manager() -> FeedbackManager:
    dsn = os.environ.get(DSN_VARIABLE)
    if not dsn:
        return FeedbackManager()
    from postgres_feedback_store import PostgresFeedbackStore

    return FeedbackManager(store=run(PostgresFeedbackStore.open(dsn)))


def submit_form(manager: FeedbackManager) -> None:
    st.subheader("Submit feedback")
    with st.form("submit", clear_on_submit=True):
        left, middle, right = st.columns(3)
        source = left.selectbox("Source", FeedbackSource.known_values())
        category = middle.selectbox("Category", FeedbackCategory.known_values(), index=3)
        target_type = right.selectbox("Target type", FeedbackTargetType.known_values(), index=10)
        target_id = st.text_input("Target ID", value="gen-1")
        rating = st.slider("Rating", min_value=1, max_value=5, value=4)
        comment = st.text_area("Comment", placeholder="What happened, and was it right?")
        if st.form_submit_button("Submit", type="primary"):
            feedback = run(
                manager.submit(
                    source=source,
                    category=category,
                    target=FeedbackTarget(type=target_type, id=target_id.strip() or "unknown"),
                    payload={"rating": rating, "comment": comment},
                )
            )
            st.success(f"Recorded {feedback.feedback_id} ({feedback.status})")


def lifecycle_actions(manager: FeedbackManager) -> None:
    st.subheader("Review")
    # The 50 newest open events: query each open status, so closed ones never crowd them out.
    open_feedback = sorted(
        (
            event
            for status in OPEN_STATUSES
            for event in run(
                manager.query(FeedbackQuery(status=status, newest_first=True, limit=50))
            )
        ),
        key=lambda event: event.created_at,
        reverse=True,
    )[:50]
    if not open_feedback:
        st.info("No open feedback.")
        return
    by_id = {str(event.feedback_id): event for event in open_feedback}
    # The key keeps the reviewer's choice while other sessions change the list.
    # Without a default, a choice that another session closes becomes no choice.
    choice = st.selectbox(
        "Feedback",
        list(by_id),
        index=None,
        format_func=lambda key: event_label(by_id[key]),
        key="review_selection",
        placeholder="Choose feedback to review",
    )
    actions = {
        "Acknowledge": manager.acknowledge,
        "Mark handled": manager.mark_handled,
        "Resolve": manager.resolve,
        "Reject": manager.reject,
    }
    for column, (label, action) in zip(st.columns(len(actions)), actions.items(), strict=True):
        if not column.button(label):
            continue
        if choice is None:
            st.warning("Choose the feedback to review first; it may have been closed meanwhile.")
            continue
        try:
            run(action(by_id[choice].feedback_id))
        except FeedbackLifecycleError as error:
            st.error(str(error))
        else:
            st.rerun()


def feed(manager: FeedbackManager) -> None:
    st.subheader("Feed")
    status = st.selectbox("Status", ["all", *(status.value for status in FeedbackStatus)])
    query = FeedbackQuery(
        status=None if status == "all" else FeedbackStatus(status), newest_first=True, limit=200
    )
    events = run(manager.query(query))
    if not events:
        st.info("Nothing matches this filter.")
        return
    st.dataframe(
        [
            {
                "created": event.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                "status": event.status.value,
                "source": str(event.source),
                "category": str(event.category),
                "target": f"{event.target.type}:{event.target.id}",
                "payload": event.payload,
            }
            for event in events
        ],
        width="stretch",
    )


def main() -> None:
    st.set_page_config(page_title="Feedback", layout="wide")
    st.title("Feedback")
    backend = "PostgreSQL store" if os.environ.get(DSN_VARIABLE) else "in-memory store"
    st.caption(f"Backed by feedback-manager with the {backend}.")
    manager = feedback_manager()
    submit_form(manager)
    st.divider()
    lifecycle_actions(manager)
    st.divider()
    feed(manager)


main()
