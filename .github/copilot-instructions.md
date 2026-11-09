# Feedback Manager contributor guidance

- Preserve the feedback lifecycle and public store contracts. Feedback is
  authoritative; learning consumes public events, queries or optional change feeds.
- Keep core code framework-independent. LangChain and LangGraph share the
  `langchain` extra and `integrations/frameworks/langchain` package. Future
  framework adapters implement the ABCs in `integrations/base.py`.
- Every extension point is an `abc.ABC`; never add `typing.Protocol` contracts.
  Ship a contract kit in `feedback_manager.testing` for each storage-like ABC.
- Use `agxai` through `ProvenanceProvider` (`AgxaiProvenanceProvider`,
  `create_agxai_runtime`), and let callers inject every agxai, LanceDB, and
  LadybugDB component.
- Every learned artifact, job and projection has an explicit tenant and namespace.
  Private artifacts also require the applicable agent. Hosts authenticate scope.
  Unknown scope must not become shared learning.
- Learning creates candidates. Human review, independent evidence and explicit
  activation are separate from feedback approval. Never silently rewrite feedback.
- Vector and graph records are rebuildable hints, not authority. Revalidate
  current artifact versions, review policy and applicability before returning them.
- Do not claim exactly-once processing, distributed transactions or durable
  learning with memory stores. Test restart recovery with durable implementations.
- Use typed models, explicit errors and the existing structured logger. Keep
  feedback text, secrets and exception contents out of telemetry.
- Packaging uses uv_build and uv; documentation uses Zensical.
- Validate changes with `uv run ruff check .`, `uv run ruff format --check .`,
  `uv run pyrefly check`, targeted pytest, and then the full coverage gate as
  appropriate. Install all extras for database and telemetry tests.
- Build docs with `uv run zensical build --strict`; verify generated diagrams with
  `node scripts/render-diagrams.mjs --check`. Do not publish packages from a coding task.
