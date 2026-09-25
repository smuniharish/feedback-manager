# FAQ

## Is this an agent framework?

No. It is a feedback library. LangChain and LangGraph still own runtime execution, orchestration, callbacks, interrupts, checkpointing, and streaming.

## Why is `langgraph-xai` mandatory?

Because the package ships a real provenance adapter as a first-class feature, not an optional stub. See [ADR 0001](../architecture/adr/0001-langgraph-xai-mandatory-dependency.md).

## Can I use a different store?

Yes. Implement `FeedbackStore` and pass it into `FeedbackManager(store=...)`.

## Can I add my own sources or categories?

Yes. `FeedbackSource`, `FeedbackCategory`, and `FeedbackTargetType` are open string types.

## Does the package own human approval workflows?

No. It records and routes approval-related feedback, but the application still owns business workflow, UI, and final action.

## Does it capture provenance automatically everywhere?

No. The bundled adapter can attach provenance live during an active `langgraph-xai` run, or later by `run_id` if a provenance store is available.

