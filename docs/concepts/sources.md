# Sources

`FeedbackSource` answers: **who or what produced this feedback?**

Built-in values:

- `human`
- `agent`
- `generation`
- `tool`
- `evaluator`
- `application`
- `system`
- `external`

`FeedbackSource` is an open string type, so you can define your own values:

```python
from feedback_manager import FeedbackSource

source = FeedbackSource("mcp_server")
```

Use a source to distinguish provenance of the *feedback itself*, not the execution target it refers to.

See also:

- [Categories](categories.md)
- [Domain model](../architecture/DOMAIN_MODEL.md)

