# How-to guides

Task-oriented recipes for integrating feedback-manager into an application.
Each guide is self-contained and links to the concepts behind it.

| Guide | You will |
|---|---|
| [Record LangChain failures](langchain-failures.md) | Turn tool, model, retriever, and node failures into feedback automatically, without changing your error handling. |
| [Record human-in-the-loop decisions](langgraph-hitl.md) | Keep a queryable, auditable record of every LangGraph interrupt and the reviewer's answer. |
| [Link feedback to provenance](xai-provenance.md) | Connect feedback to the `langgraph-xai` run, decision, and evidence it is about. |
| [Store feedback in your database](custom-store.md) | Implement the four-method store contract with database guarantees. |
| [Route feedback to handlers](routing.md) | Send low evaluator scores and corrections to a review queue. |
| [Configure failure isolation](failure-isolation.md) | Decide, per stage, whether a failure is logged or raised. |
| [Expire stale feedback](expire-feedback.md) | Close feedback that stayed pending too long, from a periodic job. |
| [Test your integration](testing.md) | Assert on recorded feedback in unit tests, with no infrastructure. |
