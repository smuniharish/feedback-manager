---
name: feedback-manager-extend
description: >-
  Use this skill when extending feedback-manager with your own components: a
  FeedbackStore on PostgreSQL, MySQL, MongoDB, DynamoDB, or another database;
  a FrameworkAdapter for Agno, CrewAI, AutoGen, Semantic Kernel, or a custom
  agent loop; an ArtifactRepository, LearningJobStore, VectorStore, GraphStore,
  or EmbeddingProvider for the learning engine; a feedback change source; or
  a custom agxai provenance store passed through to feedback-manager. Also use
  it to certify such a component with feedback_manager.testing, or to debug a
  failing contract test or a FeedbackConfigurationError for a component. Do
  not use it to add feedback capture to an application (use feedback-manager)
  or to configure learning and retrieval (use feedback-manager-learning).
license: Apache-2.0
compatibility: >-
  Python 3.12 or newer with feedback-manager 0.1.x. The templates need no
  agent framework or database; scripts/check_component.py runs offline.
metadata:
  version: "0.1.2"
  documentation: "https://feedback-manager.readthedocs.io/en/latest/how-to/customize-components/"
---

# Extending feedback-manager

Every collaborator of `FeedbackManager` and the learning engine is an abstract
base class. Subclass it, inject the instance, and certify it with the matching
contract kit in `feedback_manager.testing`. Nothing else in the application
changes. Objects that only look like a component are rejected with
`FeedbackConfigurationError`.

| You are writing | Subclass | Certify with |
|---|---|---|
| Feedback storage | `feedback_manager.contracts.FeedbackStore` | `FeedbackStoreContract` |
| A framework integration | `feedback_manager.integrations.base.FrameworkAdapter` | [assets/test_adapter.py](assets/test_adapter.py) |
| Artifact storage | `feedback_manager.storage.artifacts.ArtifactRepository` | `ArtifactRepositoryContract` |
| A learning job queue | `feedback_manager.learning.LearningJobStore` | `LearningJobStoreContract` |
| A vector database | `feedback_manager.storage.vector.VectorStore` | `VectorStoreContract` |
| A graph database | `feedback_manager.storage.graph.GraphStore` | `GraphStoreContract` |
| An embedding model | `feedback_manager.embeddings.EmbeddingProvider` | `EmbeddingProviderContract` |
| agxai provenance storage | `agxai.ProvenanceStore` | `agxai.testing.ProvenanceStoreContract` |

Handlers, routers, policies, subscribers, sinks, and scope resolvers are
covered in [references/components.md](references/components.md).

## Workflow: a storage backend

1. Copy [assets/store_template.py](assets/store_template.py). Replace each
   `self._rows` operation with the database statement its comment names. Keep
   the compare-and-set in `transition`, tenant-scoped idempotency in `create`,
   and creation order in `query`.
2. Copy [assets/test_store_contract.py](assets/test_store_contract.py) and
   make `make_store` return a new, empty store on a disposable database.
3. Run the tests, or from the project root:

   ```bash
   python scripts/check_component.py myapp.storage:PostgresFeedbackStore
   ```

   Fix the first broken promise, then rerun until all hold. Read
   [references/store.md](references/store.md) when a failure is unclear.
4. Inject it: `FeedbackManager(store=PostgresFeedbackStore(...))`. For durable
   learning, also implement `FeedbackChangeSource` on the same store.

The same steps work for every row of the table above: the checker finds the
contract from the component's base class.

## Workflow: a framework adapter

1. Find the framework's run or config object, the object it pauses on, how it
   resumes, and how it accepts extra messages, in the version the project
   pins. Do not invent callbacks.
2. Copy [assets/adapter_template.py](assets/adapter_template.py). Replace
   `RunInfo`, `Pause`, and `Resume` with the framework's types and map its
   identifiers onto `ExecutionContext` fields.
3. Copy [assets/test_adapter.py](assets/test_adapter.py), point it at your
   adapter, and run it until it passes.
4. Read [references/framework-adapter.md](references/framework-adapter.md)
   for failure capture, provenance, and packaging as an optional extra.

## Checklist before you finish

- [ ] The component subclasses its base class and is injected, not patched in.
- [ ] Every contract test passes against the real backend.
- [ ] An adapter returns the framework's results and raises its exceptions unchanged.
- [ ] Learned context is rendered with `render_learned_context`, never spliced
      into instructions.
- [ ] The project's own tests pass.

## Gotchas

- `transition` must be atomic: compare the stored status with `expected` and
  update in one statement or transaction, or concurrent moves both succeed.
- Idempotency keys are unique per tenant: the same key in two tenants is two
  events.
- Raise the package's errors (`FeedbackNotFoundError`,
  `FeedbackConflictError`, `FeedbackStoreError`); the manager relies on them.
- Every kit calls the factory once per test. Return an empty component each
  time.
- Vector and graph stores declare `capabilities`; claim only what the
  backend does. Unsupported requests must raise, not degrade silently.
- A component built on an injected client must not close that client.
- psycopg's async mode on Windows needs
  `asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)`.

## Files

- [assets/store_template.py](assets/store_template.py) and
  [assets/test_store_contract.py](assets/test_store_contract.py): a store
  shaped like one table, and the contract it must pass.
- [assets/adapter_template.py](assets/adapter_template.py) and
  [assets/test_adapter.py](assets/test_adapter.py): an adapter to copy, and
  its tests.
- `scripts/check_component.py`: runs the matching contract against any
  component factory without pytest. `--json` prints one object. Exit codes
  are 0 (all pass), 1 (a promise broken), and 2 (factory not loadable or not
  a supported component).
- [references/store.md](references/store.md),
  [references/framework-adapter.md](references/framework-adapter.md),
  [references/components.md](references/components.md).
