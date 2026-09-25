# Custom source and category values

`FeedbackSource`, `FeedbackCategory`, and `FeedbackTargetType` are open string types.

```python
from feedback_manager import FeedbackCategory, FeedbackSource, FeedbackTargetType

source = FeedbackSource("mcp_server")
category = FeedbackCategory("business_policy_violation")
target_type = FeedbackTargetType("dataset_row")
```

You do not need to subclass anything or patch the package to introduce application-specific values.

