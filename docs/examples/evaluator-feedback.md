# Example: evaluator feedback

Source file: `examples/05_evaluator_feedback.py`

This example records evaluator output about a generation without pretending the evaluator itself belongs to `feedback-manager`.

Key pattern:

```python
feedback = await manager.submit(
    source=FeedbackSource.EVALUATOR,
    category=FeedbackCategory.QUALITY,
    target=FeedbackTarget(type=FeedbackTargetType.GENERATION, id=generation_id),
    payload=result,
    metadata={"evaluator_name": "factuality_judge_v1"},
)
```

