# Example: human correction

An agent generates a wrong answer; a human corrects it. The correction is
submitted as feedback correlated with the generation that produced the
original answer, then walked through `acknowledge -> mark_handled ->
resolve` once the correction has been applied downstream.

Full source, embedded directly from `examples/01_human_correction.py` so it
can never drift out of sync with the docs:

```python title="examples/01_human_correction.py"
--8<-- "examples/01_human_correction.py"
```

## Real run

```console
$ uv run python examples/01_human_correction.py
Agent answered: 'The capital of Australia is Sydney.'
Correction recorded: id=a4d445f0-ea68-4567-bcea-69169e0aef37 status=received
Correction resolved: status=resolved metadata={'resolution': {'applied': True, 'channel': 'manual_review'}}
```

Set `FEEDBACK_MANAGER_POSTGRES_DSN` to run the same code against a real
PostgreSQL store instead of the zero-config in-memory default -- see
[Real Grafana observability](grafana-observability.md).
