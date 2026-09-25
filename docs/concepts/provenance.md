# Provenance

`feedback-manager` does not capture provenance itself. It consumes it through the provenance adapter boundary.

Bundled support:

- `XAIProvenanceAdapter` for `langgraph-xai`

Core feedback records store provenance as `FeedbackProvenanceReference`, a framework-independent model.

The bundled adapter can:

- attach provenance live from `XAIRuntime.current_run` while a graph node is executing
- resolve provenance later by `run_id` through `registry.get(ProvenanceStore)`

If neither source exists, provenance remains `None`.

Deep dive: [Provenance architecture](../architecture/PROVENANCE_MODEL.md).

