"""The default, rule-based :class:`FeedbackRouter` implementation."""

from __future__ import annotations

from collections.abc import Sequence

from feedback_manager.contracts.handler import FeedbackHandler
from feedback_manager.contracts.router import FeedbackRouter
from feedback_manager.core.events import FeedbackEvent
from feedback_manager.routing.rules import RoutingRule


class DefaultFeedbackRouter(FeedbackRouter):
    """Evaluates an ordered list of :class:`RoutingRule` objects.

    Every rule whose predicate matches contributes its handlers (in rule
    order, then handler order, de-duplicated by identity). If no rule
    matches, ``default_handlers`` is used instead -- an event is never
    silently dropped by the router.
    """

    def __init__(
        self,
        rules: Sequence[RoutingRule] = (),
        *,
        default_handlers: Sequence[FeedbackHandler] = (),
    ) -> None:
        self._rules = list(rules)
        self._default_handlers = list(default_handlers)

    def add_rule(self, rule: RoutingRule) -> None:
        """Append a routing rule, evaluated after all previously added rules."""
        self._rules.append(rule)

    async def route(self, feedback: FeedbackEvent) -> Sequence[FeedbackHandler]:
        matched: list[FeedbackHandler] = []
        seen: set[int] = set()
        for rule in self._rules:
            if not rule.predicate(feedback):
                continue
            for handler in rule.handlers:
                if id(handler) not in seen:
                    matched.append(handler)
                    seen.add(id(handler))
        if matched:
            return matched
        return list(self._default_handlers)


__all__ = ["DefaultFeedbackRouter"]
