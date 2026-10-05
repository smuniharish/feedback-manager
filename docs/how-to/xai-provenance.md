# Link feedback to provenance

[langgraph-xai](https://github.com/smuniharish/langgraph-xai) records what a
LangGraph run did and why: node and tool executions, human interactions,
decisions, and the evidence behind them. This guide links feedback to those
records, so that "this refund was wrongly routed" points at the exact run,
decision, and evidence that routed it.

## 1. Share one runtime

Create one `XAIRuntime`, instrument your graph with it, and give the same
runtime to the manager:

```python
from langgraph_xai import XAIRuntime

from feedback_manager import FeedbackManager

xai = XAIRuntime(application_id="support-bot", tenant_id="acme", graph_id="refunds")
manager = FeedbackManager(xai_runtime=xai)

graph = xai.instrument(builder.compile())
```

## 2. Feedback during the run

Inside a node, build the execution context from the node's config. It carries
the `langgraph-xai` run ID, so the feedback refers to the active run, including
its latest decision and the evidence behind it.

```python
from langchain_core.runnables import RunnableConfig

from feedback_manager import FeedbackCategory, FeedbackSource, FeedbackTarget, FeedbackTargetType
from feedback_manager.integrations.langgraph import execution_context_from_config


async def report(state: State, config: RunnableConfig) -> State:
    feedback = await manager.submit(
        source=FeedbackSource.AGENT,
        category=FeedbackCategory.REQUEST_FOR_HUMAN,
        target=FeedbackTarget(type=FeedbackTargetType.NODE, id="assess"),
        payload={"route": state["route"]},
        execution_context=execution_context_from_config(config, node_id="assess"),
    )
    feedback.provenance.decision_id   # the run's latest decision
    feedback.provenance.evidence_ids  # the evidence it relied on
    return {}
```

!!! tip "Type the `config` parameter"
    LangGraph passes the config to nodes whose `config` parameter is annotated
    as `RunnableConfig`.

## 3. Feedback after the run

Feedback often arrives later: an evaluator scores the run, or a user reports a
problem the next day. Give it the run ID, and the adapter finds the run's
stored execution:

```python
from feedback_manager import ExecutionContext

with xai.collect_runs() as runs:
    await graph.ainvoke({"amount": 900.0})
(run,) = runs

review = await manager.submit(
    source=FeedbackSource.EVALUATOR,
    category=FeedbackCategory.QUALITY,
    target=FeedbackTarget(type=FeedbackTargetType.RUN, id=str(run.run_id)),
    payload={"score": 0.9},
    execution_context=ExecutionContext(run_id=str(run.run_id), node_id="assess"),
)
review.provenance.node_execution_id  # the execution of the "assess" node
```

Store the run ID with your application's records, such as the conversation or
the generated answer, so later feedback can refer to it.

[Example 06](../examples/basics.md#06-provenance) runs both cases:

```text
In-run feedback provenance: langgraph-xai run 273fa0c7-a518-454b-bf0b-652ebcc037fe (running): 1 node execution(s), 0 tool execution(s)
  decision=583a7e1d-0333-4782-a336-0f7e8ab93cff
  evidence=('b675f115-2902-4de0-9fed-388f412f28a8',)
Post-run feedback provenance: langgraph-xai run 273fa0c7-a518-454b-bf0b-652ebcc037fe (completed): 2 node execution(s), 0 tool execution(s)
  node execution=6e6ec809-68b3-4a34-aebe-1e685eb9304d
```

## What is available when

| Reference field | During the run | After the run |
|---|---|---|
| `run_id`, `execution_id`, `summary`, `metadata` | Yes | Yes |
| `node_execution_id` | For nodes that already finished | Yes |
| `tool_execution_id` | For tool calls that already finished | Yes |
| `human_interaction_id` | For pauses and resumes already recorded | Yes |
| `decision_id`, `evidence_ids` | Yes | No: `langgraph-xai` does not store them |

Capture feedback about a decision while the run is active if you need the
decision and evidence IDs; [provenance](../concepts/provenance.md) explains why.

## Failures and the other integrations

- Provenance resolution is the `PROVENANCE` stage of the
  [failure policy](failure-isolation.md): by default a failure is logged and
  the feedback is stored without provenance.
- [`FeedbackCallbackHandler`](langchain-failures.md) and the
  [human-in-the-loop bridge](langgraph-hitl.md) record the run ID automatically.
