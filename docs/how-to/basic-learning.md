# Learn from scoped feedback

The learning foundation can extract explicit signals, group matching feedback,
create candidate artifacts, record a separate review, and retrieve active
artifacts using structured filters. This page demonstrates explicit, manual
processing. Managers also enable [automatic background learning](background-learning.md)
by default; pass `learning=False` when you only want the manual workflow.

## Run the example

Prerequisites: Python 3.12 or newer and an installed checkout of this project.
From the repository root:

```bash
uv sync
uv run python examples/13_basic_learning.py
```

The program creates two separate feedback records and prints a candidate with
two pieces of evidence followed by the reviewed strategy:

```text
Candidate: candidate, evidence=2
Retrieved: Improvement: Search, validate results, then execute.
Search, validate results, then execute.
```

The program uses only in-memory storage and needs no credentials or cleanup.
Its full source is:

```python
--8<-- "examples/13_basic_learning.py"
```

## Scope and trust

The host must authenticate the tenant and agent, choose a trustworthy scope,
and authorize reviewers. A submitted tenant ID or reviewer name is not proof of
identity. Never expose the unscoped feedback API to untrusted tenants
without a host-enforced access-control layer. An extraction request rejects a
tenant that conflicts with the feedback's execution context, but missing tenant
information must be resolved by the host rather than guessed.

Feedback and retrieved content are untrusted data, not privileged instructions.
Learning approval is separate from feedback approval. By default, publication
requires two independent execution sources, sufficient confidence, and a
manual decision bound to the reviewed content and policy version. The host
must call `request_review`, `review`, and `activate` explicitly.

## Reliability and limitations

The bundled artifact repository, like the default feedback store, loses its
contents when the process exits. `LearningEngine.process` writes each pattern
independently, so a failed batch may have already written earlier candidates.
Replaying the same signals does not add duplicate evidence. A changed active
artifact returns to review, and `invalidate_feedback` marks evidence invalid
without erasing its provenance. That method does not install a durable
tombstone or subscribe to feedback lifecycle updates; callers must serialize
and coordinate lifecycle changes themselves.

Structured retrieval applies tenant, namespace and applicability filters,
checks the current validation policy, and bounds the rendered context. Optional
[LanceDB/LadybugDB adapters and hybrid retrieval](hybrid-retrieval.md) add vector
and graph projections. The [background workflow](background-learning.md) adds
retry, dead-letter replay, lifecycle synchronization and opt-in durable recovery.
