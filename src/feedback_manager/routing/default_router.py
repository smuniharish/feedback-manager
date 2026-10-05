"""The default, rule-based `FeedbackRouter`."""

from __future__ import annotations

from typing import TYPE_CHECKING

from feedback_manager.contracts.router import FeedbackRouter

if TYPE_CHECKING:
    from collections.abc import Sequence

    from feedback_manager.contracts.handler import FeedbackHandler
    from feedback_manager.core.events import FeedbackEvent
    from feedback_manager.routing.rules import RoutingRule


class DefaultFeedbackRouter(FeedbackRouter):
    """Routes events with an ordered list of `RoutingRule` objects.

    Every matching rule contributes its handlers, in rule order and then
    handler order, each handler at most once. When no rule matches, the
    ``default_handlers`` run instead. Without rules or default handlers, as in
    a bare `FeedbackManager()`, events are stored and published but not routed.
    """

    def __init__(
        self,
        rules: Sequence[RoutingRule] = (),
        *,
        default_handlers: Sequence[FeedbackHandler] = (),
    ) -> None:
        self._rules = list(rules)
        self._default_handlers = tuple(default_handlers)

    def add_rule(self, rule: RoutingRule) -> None:
        """Append ``rule``; it is evaluated after every existing rule."""
        self._rules.append(rule)

    async def route(self, feedback: FeedbackEvent) -> Sequence[FeedbackHandler]:
        """Return the handlers of every matching rule, or the default handlers."""
        matched: dict[int, FeedbackHandler] = {}
        for rule in tuple(self._rules):
            if rule.predicate(feedback):
                for handler in rule.handlers:
                    matched.setdefault(id(handler), handler)
        return tuple(matched.values()) if matched else self._default_handlers


__all__ = ["DefaultFeedbackRouter"]
