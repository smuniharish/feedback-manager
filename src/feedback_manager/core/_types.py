"""Shared value types for the feedback domain model.

`FeedbackSource`, `FeedbackCategory`, and `FeedbackTargetType` are *open*
values: each defines well-known constants, but applications can introduce new
values without subclassing or modifying the package. `OpenStringValue` is the
small ``str`` subclass behind them. It compares, hashes, and serializes exactly
like the string it wraps, and it rejects empty or padded strings.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Self

from pydantic import AfterValidator, JsonValue
from pydantic_core import core_schema

from feedback_manager.errors import FeedbackValidationError

if TYPE_CHECKING:
    from pydantic import GetCoreSchemaHandler

JsonObject = dict[str, JsonValue]
"""A JSON object: string keys and JSON-compatible values."""


def _check_identifier(value: str) -> str:
    if not value or value.isspace():
        raise ValueError("must be a non-empty string")
    if value != value.strip():
        raise ValueError("must not start or end with whitespace")
    return value


Identifier = Annotated[str, AfterValidator(_check_identifier)]
"""A non-empty string without leading or trailing whitespace."""


class OpenStringValue(str):
    """A validated ``str`` subclass with well-known constants and native pydantic support."""

    __slots__ = ()

    def __new__(cls, value: str) -> Self:
        """Validate ``value`` and wrap it.

        Raises:
            FeedbackValidationError: If ``value`` is not a string, is empty, or
                starts or ends with whitespace.
        """
        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            raise FeedbackValidationError(
                f"{cls.__name__} must be a string, not {type(value).__name__}"
            )
        try:
            _check_identifier(value)
        except ValueError as exc:
            raise FeedbackValidationError(f"{cls.__name__} {exc}: {value!r}") from None
        return super().__new__(cls, value)

    @classmethod
    def known_values(cls) -> tuple[Self, ...]:
        """Return the well-known values this type defines, in definition order."""
        return tuple(
            value for name, value in vars(cls).items() if name.isupper() and isinstance(value, cls)
        )

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: Any, handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        return core_schema.no_info_after_validator_function(cls, core_schema.str_schema())


__all__ = ["Identifier", "JsonObject", "OpenStringValue"]
