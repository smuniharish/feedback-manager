---
name: feedback-manager-learning
description: >-
  Use this skill when agents must improve from validated feedback with
  feedback-manager's learning engine: turning corrections and failures into
  learned artifacts, running the background learner and recovering it after
  restarts, reviewing and activating artifacts, setting learning scopes for
  tenants and agents, retrieving learned context through RetrievalLoom,
  supplying native RetrievalLoom components or embeddings, and injecting
  lessons into LangChain, LangGraph, or custom agents. Also use it when no
  artifact is created, an artifact never becomes active, retrieval returns
  nothing, or jobs are dead-lettered. Do not use it to fine-tune or train
  models, to capture or route feedback (use feedback-manager), or to write a
  new store or adapter (use feedback-manager-extend).
license: Apache-2.0
compatibility: >-
  Python 3.12 or newer with feedback-manager 0.2.x. RetrievalLoom is included
  with the core package. The legacy LanceDB and LadybugDB adapters are optional;
  the local BGE-M3 example needs the source-repository models group and local
  checkpoint. scripts/verify_learning.py runs offline and needs no extra.
metadata:
  version: "0.2.0"
  documentation: "https://feedback-manager.readthedocs.io/en/latest/how-to/basic-learning/"
---

# Learning from feedback

feedback-manager learns at the agent level: it turns validated feedback into
versioned, reviewed **learned artifacts** that agents retrieve at runtime. It
never trains or fine-tunes a model, and original feedback is never changed.

The lifecycle is: feedback → `LearningSignal` → pattern → candidate
`LearnedArtifact` → review → `ACTIVE` → retrieval → the agent's next run.

## Before you change anything

1. Run the offline check from this skill's directory, in the project's Python
   environment:

   ```bash
   python scripts/verify_learning.py
   ```

   Fix every `FAIL` line first.
2. Find the existing `FeedbackManager`, its stores, and where the agent builds
   its prompt. Learned context goes there.
3. Decide the scope: which tenant, namespace, and agent each lesson belongs to.

## Workflow

1. Give the manager a learning engine on a repository you control:

   ```python
   from feedback_manager import FeedbackManager
   from feedback_manager.learning.engine import LearningEngine
   from feedback_manager.learning.assessment_verification import AssessmentVerifier
   from feedback_manager.learning.worker import ContextScopeResolver
   from feedback_manager.storage import InMemoryFeedbackStore
   from feedback_manager.storage.artifacts import InMemoryArtifactRepository
   from feedback_manager.storage.assessment import InMemoryAssessmentRepository
   from feedback_manager.storage.experience import InMemoryExperienceRepository

   repository = InMemoryArtifactRepository()  # a durable repository in production
   feedback_store = InMemoryFeedbackStore()
   verifier = AssessmentVerifier(
       feedback_store, InMemoryExperienceRepository(), InMemoryAssessmentRepository()
   )
   engine = LearningEngine(repository, assessment_verifier=verifier)
   manager = FeedbackManager(
       store=feedback_store,
       learning_engine=engine,
       learning_scope_resolver=ContextScopeResolver(namespace="default"),
   )
   ```

2. Submit feedback with a tenant and an agent. `for_tenant` sets the tenant;
   the execution context names the agent and run:

   ```python
   await manager.for_tenant("acme").submit(
       source="human",
       category="correction",
       target=FeedbackTarget(type=FeedbackTargetType.RUN, id=run_id),
       payload={"correction": "Validate tool results before acting."},
       execution_context=ExecutionContext(agent_id="researcher", run_id=run_id),
       idempotency_key=f"correction:{run_id}",
   )
   ```

3. Independently assess the feedback against its archived experience and
   supporting evidence, and record a separate acceptance decision. Enqueue
   the accepted assessment with `await learner.enqueue_assessment(feedback_id,
   assessment_id, assessment_version=version)`. At startup, call
   `await learner.recover_assessments(assessments)` to replay accepted decisions
   whose job was lost in a crash; it reads the repository's commit-ordered
   decision feed under its own checkpoint.
   In tests and scripts, call `await learner.drain()`.
4. Review. Artifacts start as `CANDIDATE` and are never retrieved until a
   person approves them:

   ```python
   scope = ArtifactScope(tenant_id="acme", agent_id="researcher")
   await engine.request_review(scope, artifact_id)
   await engine.review(scope, artifact_id, reviewer=user_id, approved=True)
   await engine.activate(scope, artifact_id)
   ```

5. Retrieve and inject before the agent runs:

   ```python
   from feedback_manager.integrations.frameworks.custom import CustomAgentAdapter
   from feedback_manager.retrieval import RetrievalRequest

   request = RetrievalRequest(scope=scope, agent_id="researcher", task=task)
   context = await CustomAgentAdapter().context_injector(engine).build(request)
   ```

   `context` is `None` when nothing applies. For LangChain and LangGraph,
   `LangChainAdapter().context_injector(engine)` returns a message to prepend.
6. Test the flow with [assets/test_learning_flow.py](assets/test_learning_flow.py),
   then run the project's tests and `scripts/verify_learning.py` again.

Read [references/learning.md](references/learning.md) for signals, evidence,
validation, and review; [references/retrieval.md](references/retrieval.md) for
RetrievalLoom, custom embeddings, and legacy adapters; and
[references/operations.md](references/operations.md) for durability, retries,
and recovery.

## Rules

- **Never auto-activate.** Publication needs independent evidence and an
  explicit review. Do not call `review(..., approved=True)` from code that
  runs without a person.
- **Authenticate the scope.** The host proves the tenant, agent, and reviewer.
  A tenant ID in a payload is not proof.
- **Treat learned context as data.** Keep the injector's header and
  delimiters, and keep learned context in its own message, separate from your
  instructions.
- **Make durability explicit.** In production use a durable feedback store
  with a change source, a durable `LearningJobStore`, and a durable
  `ArtifactRepository`, plus durable experience and assessment repositories;
  pass `require_durable_learning=True`.
- **Never mix embedding spaces.** Changing the model, revision, dimensions,
  normalization, or instructions creates a new space: reindex into it.
- **Keep learning on unless it runs elsewhere.**
  `FeedbackManager(learning=False)` turns off the background worker.

## Gotchas

- One separately assessed and accepted feedback record can give a candidate
  with one evidence item. The default
  validation policy needs two independent runs: `review` raises
  `LearningValidationError` until then.
- The default scope resolver requires a host-supplied namespace, tenant and
  agent; unknown scope is quarantined.
- An artifact for agent `researcher` is not retrieved for another agent or
  tenant. That is isolation working.
- `drain()` does not wait for delayed retries. Inspect dead letters in the
  job store and requeue them with `replay_dead_letters()`.
- The legacy LadybugDB adapter allows one writer process. If you use it, index
  from one process per tenant and namespace.

## Files

- `scripts/verify_learning.py`: offline end-to-end check. Exit code 0 when
  every check passes, 1 otherwise; `--json` prints one object.
- [assets/test_learning_flow.py](assets/test_learning_flow.py): pytest
  template covering learning, review, retrieval, and isolation.
- [references/learning.md](references/learning.md),
  [references/retrieval.md](references/retrieval.md),
  [references/operations.md](references/operations.md).
- Documentation: <https://feedback-manager.readthedocs.io>
