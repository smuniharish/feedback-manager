# Example: HITL approval

Uses a real, compiled LangGraph graph with a native `interrupt`/`Command(resume=...)`
cycle. LangGraph owns pausing and resuming execution; `feedback-manager`
only manages the feedback record describing *why* execution paused and
what a human decided, via `HumanInTheLoopBridge`.

Full source, embedded directly from `examples/02_hitl_approval.py`:

```python title="examples/02_hitl_approval.py"
--8<-- "examples/02_hitl_approval.py"
```

## Real run

```console
$ uv run python examples/02_hitl_approval.py
Graph paused, asking a human: {'question': 'Approve sending this email to the customer?', 'action': 'send_refund_email'}
Feedback request recorded: id=e41dc2fd-1490-408f-8013-c70c0829308e status=received
Human decision recorded: status=resolved
Graph resumed and finished: {'action': 'send_refund_email (approved)'}
```

