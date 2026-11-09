<!--
Thanks for contributing to feedback-manager! Please fill out this template;
it helps reviewers understand and validate your change quickly.
-->

## Summary

<!-- What does this PR change, and why? -->

## Related issue(s)

<!-- Link any related issues, e.g. "Closes #123". -->

## Type of change

- [ ] Bug fix
- [ ] New feature (abstract base class, store, adapter, learning or retrieval capability)
- [ ] Documentation
- [ ] Refactor / internal cleanup (no behavior change)
- [ ] Other (describe above)

## Checklist

- [ ] I read [CONTRIBUTING.md](../CONTRIBUTING.md).
- [ ] New/changed public behavior is covered by tests.
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass.
- [ ] `uv run pyrefly check` passes.
- [ ] `uv run pytest --cov` passes with 100% coverage.
- [ ] `uv run zensical build --strict` and `uv run python scripts/check_site_links.py` pass, and diagrams were re-rendered if
      a `diagrams/*.mmd` source changed.
- [ ] Relevant docs under `docs/` were updated (if this changes public API,
      configuration, or behavior).
- [ ] `CHANGELOG.md` has an entry under `[Unreleased]` (if this is a
      user-visible change).

## Feedback and learning impact

<!--
Does this change what feedback is stored, how its lifecycle moves, what is
learned, or what reaches an agent at runtime? Describe the effect, or write
"None". New components should subclass an existing abstract base class (such
as FeedbackStore or VectorStore) without changes to FeedbackManager.
-->
