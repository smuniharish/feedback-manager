# Domain model

The framework-independent model in `feedback_manager.core`. The most used
names are also available from `feedback_manager`.

## Events

::: feedback_manager.core.FeedbackEvent

::: feedback_manager.core.FeedbackTarget

::: feedback_manager.core.ExecutionContext

::: feedback_manager.core.FeedbackProvenanceReference

## Open values

Sources, categories, and target types are strings with well-known constants.
Any other non-empty string without surrounding whitespace is valid too.

::: feedback_manager.core.FeedbackSource
    options:
      show_bases: false
      inherited_members: [known_values]

::: feedback_manager.core.FeedbackCategory
    options:
      show_bases: false
      inherited_members: [known_values]

::: feedback_manager.core.FeedbackTargetType
    options:
      show_bases: false
      inherited_members: [known_values]

## Lifecycle

::: feedback_manager.core.FeedbackStatus

::: feedback_manager.core.TERMINAL_STATUSES

::: feedback_manager.core.LEGAL_TRANSITIONS

::: feedback_manager.core.is_legal_transition

::: feedback_manager.core.validate_transition

::: feedback_manager.core.LifecycleTransition

## Types

::: feedback_manager.core.Identifier
    options:
      show_attribute_values: false

::: feedback_manager.core.JsonObject
