"""Internal ``structlog`` wiring for the package's log output.

Library code must not call ``structlog.configure()``: that is an application
decision. Each logger here wraps the standard-library logger of the same name,
so applications that configure ``logging`` (handlers, levels, filters, pytest's
``caplog``) receive feedback-manager's structured messages with no extra setup.
Disabled levels are dropped before any rendering work, and ``exc_info`` is
handed to the standard-library logger, which formats tracebacks as usual.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

import structlog

if TYPE_CHECKING:
    from collections.abc import MutableMapping

_RENDERER = structlog.processors.KeyValueRenderer(key_order=["event"])


def _render(
    logger: Any, method_name: str, event_dict: MutableMapping[str, Any]
) -> tuple[tuple[str], dict[str, Any]]:
    exc_info = event_dict.pop("exc_info", None)
    message = _RENDERER(logger, method_name, event_dict)
    return (message,), ({"exc_info": exc_info} if exc_info is not None else {})


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a ``structlog`` logger that writes to the standard-library logger ``name``."""
    return cast(
        "structlog.stdlib.BoundLogger",
        structlog.wrap_logger(
            logging.getLogger(name),
            wrapper_class=structlog.stdlib.BoundLogger,
            processors=[structlog.stdlib.filter_by_level, _render],
        ),
    )


__all__ = ["get_logger"]
