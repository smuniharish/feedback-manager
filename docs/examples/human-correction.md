# Example: human correction

Source file: `examples/01_human_correction.py`

This example shows a human correcting a bad generation and then completing the lifecycle.

Key pattern:

```python
async def apply_correction() -> None:
    correction = await manager.submit(
        source=FeedbackSource.HUMAN,
        category=FeedbackCategory.CORRECTION,
        target=FeedbackTarget(type=FeedbackTargetType.GENERATION, id=generation_id),
        payload={
            "original_text": answer,
            "corrected_text": "The capital of Australia is Canberra.",
        },
        execution_context=ExecutionContext(generation_id=generation_id),
    )

    await manager.acknowledge(correction.feedback_id)
    await manager.mark_handled(correction.feedback_id)
    await manager.resolve(correction.feedback_id, resolution={"applied": True})
```
