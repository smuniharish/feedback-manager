"""Internal ``structlog`` wiring shared by the bundled logging sinks/handlers.

Library code must not call ``structlog.configure()`` globally -- that is an
application-level decision. Each logger created here instead wraps the
equivalent stdlib :class:`logging.Logger` directly via
:func:`structlog.wrap_logger`, so host applications that configure Python's
standard ``logging`` module (handlers, filters, levels, ``caplog`` in tests,
...) continue to see feedback-manager's structured log output without any
extra wiring.
"""

from __future__ import annotations

import logging
from typing import Any

import structlog


def get_logger(name: str) -> Any:
    """Return a ``structlog`` logger bound to the stdlib logger named ``name``."""
    return structlog.wrap_logger(
        logging.getLogger(name),
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.KeyValueRenderer(key_order=["event"]),
        ],
    )


__all__ = ["get_logger"]
