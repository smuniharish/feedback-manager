# Example: HITL approval

Source file: `examples/02_hitl_approval.py`

This example uses a real LangGraph graph with a native interrupt/resume cycle and stores a feedback record describing the human approval request.

Key pattern:

```python
decision = HumanInTheLoopBridge.interrupt({"question": "...", "action": state["action"]})
...
feedback = await bridge.request(
    target=FeedbackTarget(type=FeedbackTargetType.GRAPH, id="hitl-example-1"),
    prompt=prompt,
)
resolved = await bridge.resolve(feedback.feedback_id, response="approved", approved=True)
result = await compiled.ainvoke(bridge.resume_command("approved"), config=config)
```

