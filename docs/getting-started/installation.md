# Installation

feedback-manager supports Python 3.12–3.14.

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
| `lancedb` | `lancedb` | Legacy LanceDB compatibility adapter; not required by default RetrievalLoom search. |
| `ladybugdb` | `ladybug` | Legacy LadybugDB compatibility adapter; not required by default RetrievalLoom search. |
| `otel` | OpenTelemetry API and SDK | Export feedback and operation telemetry through the host's configured providers. |
| `postgres` | `psycopg[binary,pool]`, `psycopg-pool` | Multi-process PostgreSQL [experience, assessment and payload stores](../how-to/background-learning.md#postgresql-stores). |
| `all` | All of the above | Trying everything. |

```bash
pip install "feedback-manager[langchain]"
```

Custom Python agents need no extra: use
[`feedback_manager.integrations.frameworks.custom`](../how-to/custom-agents.md).

## Dependencies

| Package | Version | Why |
|---|---|---|
| [`agxai`](https://pypi.org/project/agxai/) | `>=1.0.3,<2` | Provenance records that feedback can link to. |
| [`pydantic`](https://pypi.org/project/pydantic/) | `>=2.14.3,<3` | Validated, immutable domain models. |
| [`retrievalloom`](https://pypi.org/project/retrievalloom/) | `>=0.2.0,<0.3` | The package's retrieval engine; installed automatically with the core package. |
| [`structlog`](https://pypi.org/project/structlog/) | `>=26.1.0,<27` | Structured log records for the default observability sink. |

Importing `feedback_manager` loads no agent framework. An adapter imports its
framework only when you import the adapter, and raises an `ImportError` naming
the extra to install when the framework is missing.

## Verify the installation

```bash
python -c "import feedback_manager; print(feedback_manager.__version__)"
```

```text
0.2.0
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
