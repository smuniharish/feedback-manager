# Correlation

A correlation ID groups feedback that belongs together, such as all feedback
about one run or one conversation. The correlator assigns it once, when the
feedback is submitted, and it never changes afterwards.

```python
from feedback_manager import FeedbackQuery

conversation = await manager.query(FeedbackQuery(correlation_id="support-7"))
```

## The default: run, then thread, then checkpoint

`DefaultFeedbackCorrelator` uses the most specific execution identifier the
feedback carries:

1. `execution_context.run_id`, so feedback about one run shares an ID;
2. otherwise `execution_context.thread_id`, grouping a conversation;
3. otherwise `execution_context.checkpoint_id`;
4. otherwise the feedback's own `feedback_id`: feedback without execution
   context is a group of one.

Inside a `langgraph-xai` instrumented graph, `run_id` is the `langgraph-xai`
run ID, so the correlation ID matches the run's provenance records.

## A custom correlator

Any object with an `async correlate(feedback) -> str` method is a correlator.
It must return a non-empty string without leading or trailing whitespace.

```python
from feedback_manager import FeedbackEvent, FeedbackManager


class ByConversation:
    """Group feedback by the conversation ID your application keeps in metadata."""

    async def correlate(self, feedback: FeedbackEvent) -> str:
        conversation = feedback.metadata.get("conversation_id")
        if isinstance(conversation, str) and conversation:
            return f"conversation:{conversation}"
        return str(feedback.feedback_id)


manager = FeedbackManager(correlator=ByConversation())
```

If the correlator raises, or returns an invalid ID, the `CORRELATION` stage's
[failure mode](../how-to/failure-isolation.md) applies. By default the failure
is logged and the default derivation above is used instead, so the feedback is
still stored. In `BLOCKING` mode, `submit` raises `FeedbackCorrelationError`
and nothing is stored.

## Correlation and idempotency

They solve different problems:

- The **correlation ID** groups *different* pieces of feedback.
- The **idempotency key** recognizes the *same* piece of feedback submitted
  twice, for example after a client retry. Resubmitting with a stored key
  returns the stored event, in its current status, without processing it again.

```python
first = await manager.submit(..., idempotency_key="rating:gen-42:user-7")
retry = await manager.submit(..., idempotency_key="rating:gen-42:user-7")
assert retry.feedback_id == first.feedback_id
```
