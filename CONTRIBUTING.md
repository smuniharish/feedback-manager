# Contributing

Thank you for improving `feedback-manager`. Contributions should keep its
focus: feedback infrastructure for LangChain and LangGraph applications, not a
graph runtime, a tracing platform, or a user interface. The
[design overview](https://feedback-manager.readthedocs.io/en/latest/design/)
explains the principles changes are measured against.

## Before opening a change

1. Open an issue first for changes to the public API, the domain model, the
   lifecycle, the store contract, or an integration.
2. Keep each change focused, with tests and documentation for anything a user
   can observe.

## Development setup

Python 3.12 or later and [uv](https://docs.astral.sh/uv/) are required.

```bash
git clone https://github.com/smuniharish/feedback-manager.git
cd feedback-manager
uv sync --all-groups
```

## Checks

```bash
uv run ruff check .
uv run ruff format --check .
uv run pyrefly check
uv run pytest --cov
```

The default test run needs no services or API keys, and line and branch
coverage are kept at 100%. Property-based tests use
[Hypothesis](https://hypothesis.readthedocs.io/); set
`HYPOTHESIS_PROFILE=thorough` to run more examples locally, or `ci` as the
continuous integration does.

The PostgreSQL store tests run when `FEEDBACK_MANAGER_TEST_POSTGRES_DSN` points
at a database; each test creates and drops its own table:

```bash
docker compose -f examples/compose.yaml up -d postgres
FEEDBACK_MANAGER_TEST_POSTGRES_DSN=postgresql://feedback:feedback@localhost:5432/feedback \
    uv run pytest -m postgres
```

Never commit credentials, local environments, or generated reports.

## Documentation and diagrams

```bash
uv run zensical build --strict
```

The documentation embeds the example sources, so a changed example changes its
page. Diagrams are Mermaid sources under `diagrams/`, rendered to PNG with
Mermaid CLI 12.0.0. Install it globally; it needs Node.js 22.13 or later.
`--allow-scripts=puppeteer` lets Puppeteer download the headless browser the
CLI renders with:

```bash
npm install --global --allow-scripts=puppeteer @mermaid-js/mermaid-cli@12.0.0
node scripts/render-diagrams.mjs
node scripts/render-diagrams.mjs --check
```

Commit a changed diagram source together with its rendered image and the
updated `docs/assets/diagrams/manifest.json`.

The site is built with [Zensical](https://zensical.org/) from `zensical.toml`.
`uv run zensical serve` previews it with live reload.

## Pull requests

- Describe the user-visible impact.
- Add or update tests for every behavior change.
- Update the affected documentation pages and the changelog.
- Make sure formatting, lint, type checks, tests, the strict docs build, and
  the diagram check pass.

## Releases

Releases follow [semantic versioning](https://semver.org/). To publish one:

1. Set the version in `pyproject.toml` and the Agent Skill's
   `metadata.version` in
   `feedback-manager-skills/skills/feedback-manager/SKILL.md`, then run
   `uv lock`. A test fails until the two versions match.
2. Move the changelog entries under a heading with the version and date.
3. Build the distributions locally with `uv build` and check them with
   `uvx twine check --strict dist/*`.
4. Publish the verified distributions to PyPI manually, then publish the
   matching GitHub release with a `v`-prefixed tag, such as `v0.2.0`.

Releases are performed manually by a maintainer. GitHub Actions does not
publish to PyPI, and PyPI Trusted Publishing is not configured.

## Security reports

Do not report vulnerabilities in public issues. Follow the
[security policy](https://github.com/smuniharish/feedback-manager/blob/master/SECURITY.md).
