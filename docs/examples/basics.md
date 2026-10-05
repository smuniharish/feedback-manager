# Basics

Six offline examples covering the core workflows. They need no model, network,
or database: `uv sync`, then run any of them with `uv run python examples/<file>`.

## 01 Human correction

A person corrects a generated answer. The correction is recorded with the
conversation it belongs to, taken through its lifecycle, and queried back.

```python title="examples/01_human_correction.py"
--8<-- "examples/01_human_correction.py"
```

```text title="Output"
Recorded correction 059c90bb-0b18-4eb3-aded-65d13bf17927 (received)
Resolved: resolved, resolution={'applied_to': 'faq-cache'}
Feedback about thread support-7: 1 event(s)
```

## 02 Human-in-the-loop approval

A graph pauses with a native LangGraph `interrupt()` before sending a refund
email. `HumanInTheLoopBridge` records the request and the reviewer's decision,
and the graph resumes with the decision. See
[recording human-in-the-loop decisions](../how-to/langgraph-hitl.md).

```python title="examples/02_hitl_approval.py"
--8<-- "examples/02_hitl_approval.py"
```

```text title="Output"
Graph paused; approval request 5b193dcf-c6ef-4677-a983-1061d497a0fc is received
Prompt shown to the reviewer: {'question': 'Send the refund email?', 'action': 'send_refund_email'}
Decision recorded: resolved, resolution={'response': 'approved', 'approved': True}
Graph resumed and finished: {'action': 'send_refund_email', 'decision': 'approved'}
```

## 03 Tool failure

A tool times out inside a LangGraph agent. The application handles the
exception as usual, and `FeedbackCallbackHandler` records one `TOOL` event for
the failing tool call, with its thread and node. See
[recording LangChain failures](../how-to/langchain-failures.md).

```python title="examples/03_tool_failure.py"
--8<-- "examples/03_tool_failure.py"
```

```text title="Output"
The agent failed as usual: weather service did not answer for 'Canberra' within 5 seconds
Recorded tool/timeout feedback about tool_call 'call-weather-1' (thread=weather-chat-3, node=tools)
  payload: {'error': "weather service did not answer for 'Canberra' within 5 seconds", 'error_type': 'TimeoutError', 'operation': 'fetch_weather'}
```

## 04 Generation interruption

A user stops a streaming generation halfway. The application records the
interruption with the partial output, and still lets the cancellation
propagate.

```python title="examples/04_generation_interruption.py"
--8<-- "examples/04_generation_interruption.py"
```

```text title="Output"
Completed: 'Canberra is the capital of Australia.'
Generation gen-101 was stopped by the user.
gen-100: completion {'tokens': 7}
gen-101: interruption {'partial_output': 'Canberra is', 'tokens': 2}
```

## 05 Evaluator feedback

An evaluator scores three answers. A routing rule sends the low score to a
review queue. See [routing feedback to handlers](../how-to/routing.md).

```python title="examples/05_evaluator_feedback.py"
--8<-- "examples/05_evaluator_feedback.py"
```

```text title="Output"
gen-1: score=0.95 (Correct.)
gen-2: score=0.2 (Names the wrong city.)
gen-3: score=0.95 (Correct.)
Routed to human review: ['gen-2']
```

## 06 Provenance

A `langgraph-xai` instrumented graph records a routing decision and its
evidence. Feedback submitted during the run points at that decision; feedback
submitted after the run points at the stored execution. See
[linking feedback to provenance](../how-to/xai-provenance.md).

```python title="examples/06_provenance.py"
--8<-- "examples/06_provenance.py"
```

```text title="Output"
In-run feedback provenance: langgraph-xai run 273fa0c7-a518-454b-bf0b-652ebcc037fe (running): 1 node execution(s), 0 tool execution(s)
  decision=583a7e1d-0333-4782-a336-0f7e8ab93cff
  evidence=('b675f115-2902-4de0-9fed-388f412f28a8',)
Post-run feedback provenance: langgraph-xai run 273fa0c7-a518-454b-bf0b-652ebcc037fe (completed): 2 node execution(s), 0 tool execution(s)
  node execution=6e6ec809-68b3-4a34-aebe-1e685eb9304d
```

IDs differ on every run.
