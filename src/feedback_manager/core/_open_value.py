"""Shared base for "open" string-valued domain identifiers.

Several domain concepts (:class:`~feedback_manager.core.sources.FeedbackSource`,
:class:`~feedback_manager.core.categories.FeedbackCategory`,
:class:`~feedback_manager.core.targets.FeedbackTargetType`) are deliberately
*not* closed ``enum.Enum`` types: the spec requires that application code be
able to introduce new values without subclassing an enum or modifying this
package. ``OpenStringValue`` is a small ``str`` subclass that:

* behaves exactly like ``str`` for equality, hashing, and JSON;
* keeps a small set of well-known class-level constants for discoverability;
* still validates/serializes correctly through pydantic v2, which does not
  automatically support arbitrary ``str`` subclasses without an explicit
  ``__get_pydantic_core_schema__`` hook.
"""

from __future__ import annotations

from typing import Any

from pydantic import GetCoreSchemaHandler
from pydantic_core import core_schema


class OpenStringValue(str):
    """A ``str`` subclass that pydantic v2 can validate/serialize natively."""

    __slots__ = ()

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: Any, handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        return core_schema.no_info_after_validator_function(cls, core_schema.str_schema())


__all__ = ["OpenStringValue"]
