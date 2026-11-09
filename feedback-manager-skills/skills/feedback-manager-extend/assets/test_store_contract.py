"""Certify a FeedbackStore against feedback-manager's public contract.

Copy this file into your test suite and make ``make_store`` return a new,
empty store on a disposable database. It needs pytest and pytest-asyncio:

    pytest test_store_contract.py
"""

from __future__ import annotations

from feedback_manager.testing import FeedbackStoreContract

from store_template import TableFeedbackStore  # Replace with your store.


class TestTableFeedbackStore(FeedbackStoreContract):
    async def make_store(self) -> TableFeedbackStore:
        return TableFeedbackStore()  # Replace: a new, empty store per test.
