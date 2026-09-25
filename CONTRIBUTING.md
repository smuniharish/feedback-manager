# Contributing to feedback-manager

Thank you for your interest in improving `feedback-manager`. This project is
a focused feedback-infrastructure library for LangChain/LangGraph
applications — see [README.md](README.md) and
[docs/architecture/ARCHITECTURE.md](docs/architecture/ARCHITECTURE.md) for
what it is (and deliberately is not) before proposing larger changes.

## Ground rules

- **Reuse before reinventing.** Before adding infrastructure, check whether
  LangGraph, LangChain, or `langgraph-xai` already provide it. Custom code
  in this repository must stay focused on the feedback-management domain
  (see [docs/architecture/RESPONSIBILITY_MATRIX.md](docs/architecture/RESPONSIBILITY_MATRIX.md)).
- **Small, stable public API.** Changes to `feedback_manager/__init__.py`'s
  `__all__` are a big deal — discuss in an issue first.
- **Extension points are ABC/Protocol contracts**, not ad-hoc hooks. New
  extensibility should fit the existing pattern in `contracts/`.
- **No hidden global state.** Every `FeedbackManager` instance must remain
  fully independent (see Section 29 of the original design spec).

## Development setup

This project uses [`uv`](https://github.com/astral-sh/uv) and Python 3.12.

```bash
git clone https://github.com/samamuniharish/feedback-manager.git
cd feedback-manager
uv sync --all-extras
```

## Running checks locally

```bash
uv run ruff check src tests examples
uv run ruff format --check src tests examples
uv run mypy src
uv run pytest tests -q
uv run coverage run -m pytest tests -q && uv run coverage report
uv run mkdocs build --strict
```

All of the above must pass before opening a pull request. CI runs the same
checks on Python 3.12.

## Testing philosophy

- **Unit tests** (`tests/unit/`) exercise domain models, contracts, and
  reference implementations in isolation.
- **Concurrency tests** (`tests/concurrency/`) exercise concurrent
  submission, lifecycle transitions, subscribers, and shutdown/cancellation
  using standard `asyncio` primitives.
- **Integration tests** (`tests/integration/`) exercise real installed
  `langchain`, `langgraph`, and `langgraph-xai` — they must not mock these
  frameworks. If you add an integration, add a real-dependency test for it.

## Submitting changes

1. Open an issue first for anything beyond a small fix, especially anything
   touching the public API, domain model, or lifecycle state machine.
2. Keep changes surgical and scoped; avoid unrelated refactors in the same
   PR.
3. Add or update tests and documentation for any behavioral change.
4. Ensure `CHANGELOG.md` has an entry under `[Unreleased]`.
5. Follow the existing code style (`ruff format`) and typing discipline
   (`mypy --strict` on `src`).

## Reporting bugs

Please include:

- `feedback-manager`, `langchain`, `langgraph`, and `langgraph-xai`
  versions (`uv pip list` or `pip list`).
- A minimal reproduction.
- Expected vs. actual behavior.

## Security issues

Do not open a public issue for security vulnerabilities — see
[SECURITY.md](SECURITY.md).

## Code of conduct

Be respectful and constructive. Maintain a professional, collaborative
tone in issues, discussions, and pull requests.
