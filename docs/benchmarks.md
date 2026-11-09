# Benchmarks

feedback-manager runs inside your agent's process, so its cost is the latency it
adds to each call and the memory its records take. This page shows both,
measured on one machine, and how to measure them on yours.

## Results

Each scenario times one operation 1,000 times after 20 untimed warmup calls.
Every scenario uses the default in-memory stores and no observability export,
so no network time is included. Retrieval runs over 50 reviewed, active
lessons, built through the public learning workflow.

| Scenario | p50 ms | p95 ms | p99 ms | Peak memory |
| --- | ---: | ---: | ---: | ---: |
| `submit`, learning off | 0.13 | 0.21 | 0.32 | 3.5 MiB |
| `submit`, learning on | 0.21 | 0.37 | 0.49 | 5.1 MiB |
| `lifecycle`: acknowledge, handle, resolve | 0.15 | 0.29 | 0.40 | 1.4 MiB |
| `retrieve`, structured | 21.0 | 30.5 | 45.7 | 0.5 MiB |
| `retrieve`, hybrid with LanceDB | 102.1 | 132.3 | 172.4 | 1.9 MiB |

- **Recording feedback costs about 0.13 ms at p50.** Validation, correlation,
  storage, publication and routing all happen inside that call.
- **Automatic learning adds about 0.08 ms per submission.** The call only
  enqueues a job; the background learner extracts signals and builds lessons
  outside the request.
- **Retrieval is the expensive step.** Structured retrieval re-checks every
  candidate's scope, review state and applicability. Hybrid retrieval also
  embeds the task and searches LanceDB on local disk. Retrieve once per agent
  run, not once per step.
- **Peak memory is cumulative.** The in-memory store keeps every event
  submitted in a scenario, about 3.5 KiB each. Use a database
  [`FeedbackStore`](how-to/custom-store.md) in production.

## Environment

| | |
| --- | --- |
| CPU | Intel Core Ultra 7 265HX |
| OS | Windows 11 |
| Python | 3.12.16 |
| Packages | feedback-manager 0.1.2, agxai 1.0.2, pydantic 2.14.3, lancedb 0.41.0 |

Timings are taken with `tracemalloc` running, which slows every scenario by a
similar amount. Compare scenarios with each other, not with numbers from
another machine.

## Reproduce

```bash
uv sync --all-extras --all-groups
uv run python scripts/benchmark_overhead.py --runs 1000 --lessons 50
```

The script is
[`scripts/benchmark_overhead.py`](https://github.com/smuniharish/feedback-manager/blob/master/scripts/benchmark_overhead.py).
`--warmup` sets the untimed calls (default 20). Without the `lancedb` extra,
the hybrid scenario is skipped. A test in `tests/benchmarks` runs the script
with small numbers in CI and fails on order-of-magnitude regressions.
