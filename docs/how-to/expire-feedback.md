# Expire stale feedback

Feedback that nobody picks up should not stay open forever. A
`RetentionPolicy` decides how long feedback may stay pending, and
`FeedbackManager.expire_overdue` closes the overdue events as `EXPIRED`.

feedback-manager starts no background tasks, so you decide when expiry runs:
from a scheduled job, a periodic task, or an admin action.

## Expire overdue feedback periodically

```python
import asyncio
import logging
from datetime import timedelta

from feedback_manager import FeedbackManager
from feedback_manager.policies import RetentionPolicy

logger = logging.getLogger(__name__)
RETENTION = RetentionPolicy(max_pending_age=timedelta(days=7))


async def expire_stale_feedback(manager: FeedbackManager) -> None:
    while True:
        expired = await manager.expire_overdue(RETENTION)
        if expired:
            logger.info("expired %d stale feedback events", len(expired))
        await asyncio.sleep(3600)
```

Run it as a task next to your application, or call `expire_overdue` once from
a cron job or a task scheduler.

## What expires

An event is overdue when it was created more than `max_pending_age` ago and is
still `CREATED`, `RECEIVED`, or `ACKNOWLEDGED`. `HANDLED` feedback never
expires: the work is done and only awaits its resolution.

`expire_overdue` returns the events this call expired. Each one is a normal
lifecycle change: it is published to subscribers and streams, emits
`feedback.expired`, and goes through your lifecycle policy. Events that change
concurrently, are deleted, or that the lifecycle policy refuses to expire are
skipped, so several workers can run the sweep at once: each event is expired,
and returned, by one of them.

## Testing expiry

Pass `now` to see what would expire at a given time, without waiting:

```python
from datetime import UTC, datetime, timedelta

eight_days_later = datetime.now(UTC) + timedelta(days=8)
expired = await manager.expire_overdue(RETENTION, now=eight_days_later)
```

`now` must be timezone-aware. `RETENTION.cutoff(now)` returns the creation time
before which pending events are overdue, and `RETENTION.is_expired(event, now=...)`
checks a single event.

## Expiring one event

`manager.expire(feedback_id)` expires one pending event immediately, for
example when the conversation it belongs to is deleted.
