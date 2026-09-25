# Example: generation interruption

`feedback-manager` does not implement a token-streaming engine -- that's
LangChain/LangGraph's job. This example shows how an application reports
meaningful generation lifecycle events (`generation_started`,
`generation_completed`, `generation_interrupted`) as feedback so they can
be queried and correlated alongside human/tool feedback about the same
generation. Not every useful feedback event comes from a framework
callback; application code can emit domain events explicitly.

Full source, embedded directly from `examples/04_generation_interruption.py`:

```python title="examples/04_generation_interruption.py"
--8<-- "examples/04_generation_interruption.py"
```

## Real run

```console
$ uv run python examples/04_generation_interruption.py
generation_started: comment for gen-100
generation_completed: completion for gen-100
generation_started: comment for gen-101
generation_interrupted: interruption for gen-101
```

