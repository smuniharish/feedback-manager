# Example: evaluator feedback

An evaluator (LLM-as-judge, rule-based checker, or human reviewer acting as
an evaluator) scores a generation's quality. `feedback-manager` records
this feedback -- it does not implement the evaluator itself, and it does
not treat the score as ground truth; it only records what was said, by
what, and about which target.

Full source, embedded directly from `examples/05_evaluator_feedback.py`:

```python title="examples/05_evaluator_feedback.py"
--8<-- "examples/05_evaluator_feedback.py"
```

## Real run

```console
$ uv run python examples/05_evaluator_feedback.py
Evaluator feedback recorded: id=1887f7ca-fa00-43d1-a48a-80bb00193845 payload={'score': 0.42, 'critique': 'Answer is factually incorrect.', 'policy_violation': False}
Evaluator feedback resolved: status=resolved
```

