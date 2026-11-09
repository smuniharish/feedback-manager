# The feedback-manager-extend skill

The `feedback-manager-extend` skill teaches a coding agent to extend
feedback-manager through its abstract base classes. Every collaborator of
`FeedbackManager` and the learning engine is one: subclass it, inject the
instance, and certify it with the matching contract kit in
`feedback_manager.testing`. Nothing else in the application changes.

| You are writing | Subclass | Certify with |
| --- | --- | --- |
| Feedback storage | `FeedbackStore` | `FeedbackStoreContract` |
| A framework integration | `FrameworkAdapter` | the bundled adapter tests |
| Artifact storage | `ArtifactRepository` | `ArtifactRepositoryContract` |
| A learning job queue | `LearningJobStore` | `LearningJobStoreContract` |
| A vector database | `VectorStore` | `VectorStoreContract` |
| A graph database | `GraphStore` | `GraphStoreContract` |
| An embedding model | `EmbeddingProvider` | `EmbeddingProviderContract` |
| agxai provenance storage | `agxai.ProvenanceStore` | `agxai.testing.ProvenanceStoreContract` |

[:material-github: View on GitHub](https://github.com/smuniharish/feedback-manager/tree/master/feedback-manager-skills/skills/feedback-manager-extend){ .md-button }
[:material-download: Install](index.md#agentic-installation){ .md-button }

## When the agent uses it

The agent loads the skill to write, test or debug such a component: a store on
PostgreSQL, MySQL, MongoDB or DynamoDB; an adapter for Agno, CrewAI, AutoGen,
Semantic Kernel or a custom agent loop; a learning component; or a custom agxai
provenance store passed through to feedback-manager. It also loads it for a
failing contract test or a `FeedbackConfigurationError` for a component.

To add feedback capture to an application, it hands over to the
[feedback-manager skill](feedback-manager.md). To configure learning and
retrieval, it hands over to the
[feedback-manager-learning skill](feedback-manager-learning.md).

## Workflows

=== "A storage backend"

    1. Copy `assets/store_template.py`. It keeps rows in a single table, and
       each operation has a comment naming the database statement that
       replaces it.
    2. Copy `assets/test_store_contract.py` and make `make_store` return a
       new, empty store on a disposable database.
    3. Run the contract, or `scripts/check_component.py`. Fix the first broken
       promise and rerun until every promise holds.
    4. Inject it: `FeedbackManager(store=PostgresFeedbackStore(...))`.

    The same steps work for every component in the table: the checker finds
    the contract from the component's base class.

=== "A framework adapter"

    1. Find the framework's run or config object, the object it pauses on,
       how it resumes, and how it accepts extra messages, in the version the
       project pins.
    2. Copy `assets/adapter_template.py`. Replace its run, pause and resume
       types with the framework's, and map its identifiers onto
       `ExecutionContext` fields.
    3. Adapt `assets/test_adapter.py` to your adapter and run it until it
       passes.
    4. Package the adapter as an optional extra, so the core never imports
       the framework.

Before finishing, the agent checks that:

- the component subclasses its base class and is injected, not patched in;
- every contract test passes against the real backend;
- an adapter returns the framework's results and raises its exceptions
  unchanged;
- learned context is rendered with `render_learned_context`, never spliced
  into instructions.

## What's inside

```text
feedback-manager-extend/
??? SKILL.md                          # both workflows, checklist, gotchas
??? references/
?   ??? store.md                      # the store methods and their guarantees
?   ??? framework-adapter.md          # context, interrupts, failures, packaging
?   ??? components.md                 # handlers, routers, policies, sinks, and more
??? scripts/
?   ??? check_component.py            # runs the matching contract without pytest
??? assets/
?   ??? store_template.py             # a FeedbackStore shaped like one table
?   ??? test_store_contract.py        # the FeedbackStoreContract subclass
?   ??? adapter_template.py           # a FrameworkAdapter to copy
?   ??? test_adapter.py               # the tests it must pass
??? evals/                            # test prompts, assertions, trigger queries
```

The templates run as they are: their tests pass against feedback-manager
alone, so the agent starts from working code and changes one piece at a time.

## The component contract check

`scripts/check_component.py` loads a component factory, for example a class,
picks the contract kit that matches the component's base class, and runs every
test of it against new components, without pytest:

```bash
python scripts/check_component.py my_project.storage:PostgresFeedbackStore
```

```text
Checking against FeedbackStoreContract
PASS  test_create_and_get_round_trip
PASS  test_duplicate_feedback_ids_are_rejected
PASS  test_idempotency_keys_deduplicate_concurrent_creates
PASS  test_queries_filter_order_and_limit
PASS  test_resolutions_are_recorded_once
PASS  test_transitions_are_compare_and_set
All 6 promises hold.
```

It supports `FeedbackStore`, `ArtifactRepository`, `LearningJobStore`,
`VectorStore`, `GraphStore` and `EmbeddingProvider`.

| Option | Effect |
| --- | --- |
| `--path DIR` | Import the factory's module from this directory instead of the current one |
| `--json` | Print one JSON object for the agent to parse |

It exits with `0` when every promise holds, `1` when one is broken, and `2`
when the factory can't be loaded or doesn't build a supported component.

## Learn more

- [Bring your own components](../how-to/customize-components.md): every
  extension point, including a custom agxai store.
- [Store feedback in your database](../how-to/custom-store.md) and
  [integrate a custom agent framework](../how-to/custom-agents.md).
- [Testing kits](../api/testing.md): the contract kits' API.
