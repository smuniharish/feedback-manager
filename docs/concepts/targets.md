# Targets

`FeedbackTarget` answers: **what is this feedback about?**

It has three fields:

- `type`
- `id`
- `metadata`

Example:

```python
from feedback_manager import FeedbackTarget, FeedbackTargetType

target = FeedbackTarget(type=FeedbackTargetType.GENERATION, id="gen-42")
```

Built-in target types include:

- `graph`
- `thread`
- `run`
- `checkpoint`
- `node`
- `task`
- `tool_call`
- `tool_result`
- `generation`
- `message`
- `state`

`FeedbackTargetType` is also open, so applications can add their own kinds.

See the full model in [Domain model](../architecture/DOMAIN_MODEL.md).

