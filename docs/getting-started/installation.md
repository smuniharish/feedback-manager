# Installation

## Requirements

- Python `>=3.12,<3.15`

## Install from source

```powershell
uv sync
```

or with pip in an existing environment:

```powershell
pip install .
```

## Runtime dependencies

The package has mandatory runtime dependencies on:

- `langchain-core>=1.6,<2`
- `langgraph>=1.2.11,<1.3`
- `langgraph-xai>=0.1.0,<0.2`
- `pydantic>=2.12,<3`

They are installed automatically; there are no optional integration extras for the core framework boundaries.

## Development install

```powershell
uv sync --all-groups
```

That installs lint, type-check, test, and documentation dependencies.

## Verify the environment

```powershell
uv run pytest tests -q
```

## Build the docs locally

```powershell
.venv\Scripts\python.exe -m mkdocs build --strict
```

