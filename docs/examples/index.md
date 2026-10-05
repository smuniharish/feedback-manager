# Examples

Runnable programs that use feedback-manager the way an application would. Each
one is self-contained, prints what it does, and is exercised by the test suite,
so the code and output on these pages are real.

```bash
git clone https://github.com/smuniharish/feedback-manager.git
cd feedback-manager
uv sync                    # examples 01-06, 09, and the SQLite store
uv sync --group examples   # everything else
uv run python examples/01_human_correction.py
```

## Basics

Offline, deterministic, and fast: no model, no network, no database.

| Example | Shows |
|---|---|
| [01 Human correction](basics.md#01-human-correction) | Submit a correction, take it through its lifecycle, query it back. |
| [02 Human-in-the-loop approval](basics.md#02-human-in-the-loop-approval) | Record a LangGraph interrupt and the reviewer's decision, then resume. |
| [03 Tool failure](basics.md#03-tool-failure) | Record a tool timeout inside a LangGraph agent, automatically and once. |
| [04 Generation interruption](basics.md#04-generation-interruption) | Record a generation stopped mid-stream, with its partial output. |
| [05 Evaluator feedback](basics.md#05-evaluator-feedback) | Route low evaluator scores to a review queue. |
| [06 Provenance](basics.md#06-provenance) | Link feedback to `langgraph-xai` decisions and evidence. |

## Agents

Real agents with a real chat model.

| Example | Shows | Needs |
|---|---|---|
| [07 create_agent with MCP tools](agents.md#07-create_agent-with-mcp-tools) | A LangChain agent using an MCP server's tools, with failure capture and an evaluator check. | A chat model, Node.js |
| [08 deepagents with human approval](agents.md#08-deepagents-with-human-approval) | A `deepagents` file edit gated by human approval, linked to provenance. | A chat model |

## Production

Infrastructure-backed patterns.

| Example | Shows | Needs |
|---|---|---|
| [PostgreSQL store](production.md#postgresql-store) | A production `FeedbackStore` with a connection pool and compare-and-set updates. | PostgreSQL |
| [SQLite store](../how-to/custom-store.md#a-complete-sqlite-store) | A durable `FeedbackStore` from the standard library. | Nothing |
| [09 Replace every default](production.md#replace-every-default) | Every constructor argument of `FeedbackManager`, taking effect. | Nothing |
| [10 Every combination](production.md#every-combination) | All 1,560 source, category, and target type combinations. | Optionally PostgreSQL |
| [11 Grafana dashboard](production.md#grafana-dashboard) | Dashboards over the feedback table, and lifecycle annotations from a sink. | PostgreSQL, Grafana |
| [12 Realistic data](production.md#realistic-data) | Varied, organic feedback for the dashboard. | PostgreSQL |
| [Streamlit UI](production.md#streamlit-ui) | A feedback capture and review screen. | Optionally PostgreSQL |
