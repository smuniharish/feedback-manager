# Subscriptions

The package supports both pull and push consumption.

## Pull

- `get(feedback_id)`
- `query(FeedbackQuery(...))`
- `list()`

## Push

- `subscribe(async_subscriber)`
- `stream(query=None)`

Subscribers implement the structural `FeedbackSubscriber` protocol:

```python
async def subscriber(feedback: FeedbackEvent) -> None:
    ...
```

`subscribe()` returns a `Subscription` handle with:

- `cancel()`
- `active`

Subscriber failures are isolated through the failure policy.

