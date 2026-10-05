"""Coercing framework-supplied values into feedback identifiers."""

from __future__ import annotations


def text_or_none(value: object) -> str | None:
    """Return ``value`` as a stripped string, or ``None`` when it is missing or blank.

    Framework configs carry identifiers as strings, UUIDs, or integers; this
    normalizes them into the non-empty strings the domain model requires.
    """
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = ["text_or_none"]
