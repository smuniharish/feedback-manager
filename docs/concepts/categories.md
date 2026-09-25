# Categories

`FeedbackCategory` answers: **what kind of feedback is this?**

Built-in values include:

- `approval`
- `rejection`
- `correction`
- `rating`
- `comment`
- `interruption`
- `cancellation`
- `failure`
- `timeout`
- `validation`
- `quality`
- `uncertainty`
- `request_for_human`
- `partial_result`
- `completion`

Like sources, categories are open values:

```python
from feedback_manager import FeedbackCategory

category = FeedbackCategory("business_policy_violation")
```

Use `category` for semantic meaning and `feedback_type` when you need a narrower application-specific subtype.

See also:

- [Sources](sources.md)
- [Domain model](../architecture/DOMAIN_MODEL.md)

