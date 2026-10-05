# Subscriptions and streams

Two ways to react to feedback as it changes, in the same process:

- a **subscriber** is a callback that runs for every new event and every
  lifecycle change;
- a **stream** is an async iterator over the changes that match a query.

Both see the event in its new state, once per change.

```python
import asyncio

from feedback_manager import (
    FeedbackEvent,
    FeedbackManager,
    FeedbackQuery,
    FeedbackStream,
    FeedbackTarget,
)


async def main() -> None:
    manager = FeedbackManager()

    async def notify(feedback: FeedbackEvent) -> None:
        print(f"subscriber: {feedback.category} is {feedback.status}")

    async def watch(stream: FeedbackStream) -> None:
        async for feedback in stream:
            print(f"stream: {feedback.category} is {feedback.status}")

    with manager.subscribe(notify):
        async with manager.stream(FeedbackQuery(category="correction")) as stream:
            watcher = asyncio.create_task(watch(stream))
            target = FeedbackTarget(type="generation", id="gen-42")
            correction = await manager.submit(source="human", category="correction", target=target)
            await manager.submit(source="human", category="rating", target=target)
            await manager.acknowledge(correction.feedback_id)
        await watcher  # the stream ends after the events delivered before it closed


asyncio.run(main())
```

Output:

```text
subscriber: correction is received
subscriber: rating is received
subscriber: correction is acknowledged
stream: correction is received
stream: correction is acknowledged
```

## Subscribers

`manager.subscribe(callback)` registers any `async def` function or object with
an async `__call__(feedback)`. It returns a `Subscription`: call `cancel()` to
stop the notifications, or use it as a context manager, as above.

Subscribers run one after another, in the task that changed the feedback, before
`submit` or the lifecycle method returns. Keep them fast, and hand slow work to
a queue. A failing subscriber does not affect the others: its failure follows the
`SUBSCRIBER` [failure mode](../how-to/failure-isolation.md).

## Streams

`manager.stream(query)` opens a `FeedbackStream` of the events published from
then on that match `query`; `limit` and `newest_first` do not apply to streams.

- Open it from a running event loop, and consume it on that loop. Changes made
  from other tasks, threads, or event loops are delivered to it safely.
- Close it with `aclose()`, an `async with` block, or `manager.aclose()`, which
  closes every open stream. Iteration then ends after the events already
  delivered.
- Undelivered events are buffered without limit, so close streams you no longer
  read.

## What is published

| Change | Published |
|---|---|
| New feedback stored by `submit` | Yes, as `RECEIVED` |
| Resubmission with a stored idempotency key | No |
| Lifecycle change | Yes, in the new status |
| Lifecycle call that changes nothing | No |

## Order

Each change is published once, by the task that made it. Subscribers are called
in that task, and streams receive changes in the order they were published.
Changes made one after another, from any task, thread, or event loop, are
therefore delivered in order. Changes made concurrently to the same feedback,
from different tasks or threads, or by a subscriber while it handles another
change, can be delivered in a different order than the store applied them.

Every change sets a later `updated_at` than the change before it, even when the
clock has not advanced. To keep the latest state of an event from
notifications, keep the one with the latest `updated_at`, or read the current
state with `await manager.get(feedback_id)`.

Subscribers and streams deliver within one process and are not durable: the
store is the system of record. To share feedback across processes, publish
from a subscriber to your message broker, or query the store.
