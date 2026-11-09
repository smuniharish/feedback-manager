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

## Optional extras

The core is framework-agnostic. Install an extra only for the integration you use:

| Extra | Installs | Use it for |
|---|---|---|
| `langchain` | `langchain-core`, `langgraph`, `agxai[langgraph]` | The [LangChain and LangGraph adapter](../api/integrations.md#langchain-and-langgraph). LangChain and LangGraph are one framework family, so one extra covers both. |
| `lancedb` | `lancedb` | Storing learned artifacts and embeddings in LanceDB. |
| `ladybugdb` | `ladybug` | Storing artifact relationships in the LadybugDB graph database. |
| `all` | All of the above | Trying everything. |

```bash
pip install "feedback-manager[langchain]"
```

Custom Python agents need no extra: use
[`feedback_manager.integrations.frameworks.custom`](../how-to/custom-agents.md).

## Dependencies

| Package | Version | Why |
|---|---|---|
| [`agxai`](https://pypi.org/project/agxai/) | `>=1.0.2,<2` | Provenance records that feedback can link to. |
| [`pydantic`](https://pypi.org/project/pydantic/) | `>=2.14.3,<3` | Validated, immutable domain models. |
| [`structlog`](https://pypi.org/project/structlog/) | `>=26.1.0,<27` | Structured log records for the default observability sink. |

Importing `feedback_manager` loads no agent framework. An adapter imports its
framework only when you import the adapter, and raises an `ImportError` naming
the extra to install when the framework is missing.

## Verify the installation

```bash
python -c "import feedback_manager; print(feedback_manager.__version__)"
```

```text
0.1.2
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
