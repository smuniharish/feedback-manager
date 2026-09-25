# Example: provenance

Instruments a real LangGraph graph with `langgraph-xai`'s `XAIRuntime` and
attaches the resulting execution provenance to feedback submitted from
inside a running node. Pass the runtime straight to `FeedbackManager` via
`xai_runtime` -- it wires up the provenance adapter automatically.

Full source, embedded directly from `examples/06_provenance.py`:

```python title="examples/06_provenance.py"
--8<-- "examples/06_provenance.py"
```

## Real run

```console
$ uv run python examples/06_provenance.py
Provenance attached: execution_id='580f4f0c-894e-4422-aa70-43ba289713df' decision_id=None evidence_ids=() human_interaction_id=None tool_execution_id=None summary='execution status=running, nodes=0' metadata={'node_count': 0, 'tool_count': 0, 'human_interaction_count': 0, 'status': 'running'}
Graph result: {'answer': 'Canberra'}
```

