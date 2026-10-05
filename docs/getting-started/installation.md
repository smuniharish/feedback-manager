# Installation

feedback-manager requires Python 3.12 or later.

=== "pip"

    ```bash
    pip install feedback-manager
    ```

=== "uv"

    ```bash
    uv add feedback-manager
    ```

=== "Poetry"

    ```bash
    poetry add feedback-manager
    ```

## Dependencies

| Package | Version | Why |
|---|---|---|
| [`langgraph`](https://pypi.org/project/langgraph/) | `>=1.2.12,<2` | Interrupt and resume types for the human-in-the-loop bridge. Brings `langchain-core`, which the callback handler builds on. |
| [`langgraph-xai`](https://pypi.org/project/langgraph-xai/) | `>=1.0.0,<2` | Provenance records that feedback can link to. |
| [`pydantic`](https://pypi.org/project/pydantic/) | `>=2.13.5,<3` | Validated, immutable domain models. |
| [`structlog`](https://pypi.org/project/structlog/) | `>=26.1.0,<27` | Structured log records for the default observability sink. |

There are no optional extras. Importing `feedback_manager` loads none of the
framework packages: LangGraph, LangChain, and langgraph-xai are imported only
when you use an integration that needs them.

## Verify the installation

```bash
python -c "import feedback_manager; print(feedback_manager.__version__)"
```

```text
0.1.1
```

## Running the examples

The [examples](../examples/index.md) live in the source repository and have
their own dependencies (agent frameworks, an MCP client, PostgreSQL, Grafana,
and Streamlit), grouped in the `examples` dependency group:

```bash
git clone https://github.com/smuniharish/feedback-manager.git
cd feedback-manager
uv sync --group examples
uv run python examples/01_human_correction.py
```

## Next steps

- [Quickstart](quickstart.md): submit, resolve, and query your first feedback.
- [Configuration](configuration.md): replace the in-memory defaults.
