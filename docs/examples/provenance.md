# Example: provenance

Source file: `examples/06_provenance.py`

This example instruments a LangGraph graph with `XAIRuntime` and attaches provenance to feedback submitted from inside a node.

Key pattern:

```python
runtime = XAIRuntime(application_id="support-bot", tenant_id="acme-corp", graph_id="qa-graph")
manager = FeedbackManager(provenance_adapter=XAIProvenanceAdapter(runtime))
...
feedback = await manager.submit(
    source=FeedbackSource.AGENT,
    category=FeedbackCategory.COMPLETION,
    target=FeedbackTarget(type=FeedbackTargetType.NODE, id="answer_node"),
    payload={"answer": "Canberra"},
)
```

