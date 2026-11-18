# Agents

Example 07 uses a real agent and a real chat model: a LangChain agent that
uses an MCP server's tools.

## Prerequisites

```bash
uv sync --group examples
```

Example 07 builds its model with LangChain's `init_chat_model`, so any
provider it supports works. Set `FEEDBACK_MANAGER_EXAMPLE_MODEL` to a
provider-prefixed model name, plus the provider's credentials:

=== "OpenAI"

    ```bash
    export FEEDBACK_MANAGER_EXAMPLE_MODEL="openai:<model>"
    export OPENAI_API_KEY="..."
    ```

=== "OpenAI-compatible endpoint"

    ```bash
    export FEEDBACK_MANAGER_EXAMPLE_MODEL="openai:<model>"
    export OPENAI_API_KEY="..."
    export OPENAI_BASE_URL="https://<your-endpoint>/v1"
    ```

=== "Anthropic"

    ```bash
    uv pip install langchain-anthropic
    export FEEDBACK_MANAGER_EXAMPLE_MODEL="anthropic:<model>"
    export ANTHROPIC_API_KEY="..."
    ```

Model output varies between runs and models; the outputs below are from real
runs.

## 07 create_agent with MCP tools

A `langchain.agents.create_agent` agent answers a question about the files in
`examples/mcp_workspace`, using the tools of the reference filesystem MCP
server, which runs through `npx` (Node.js required). `FeedbackCallbackHandler`
records any tool or model failure, and an evaluator check of the answer is
recorded as feedback about the thread.

```python title="examples/07_agent_mcp_create_agent.py"
--8<-- "examples/07_agent_mcp_create_agent.py"
```

```text title="Output"
Loaded 14 MCP tools from the filesystem server
Tools used: ['list_allowed_directories', 'read_text_file']
Answer: The known issue is that the `/export` endpoint can time out under heavy load.
Feedback: evaluator/quality  {'check': 'mentions the /export known issue', 'passed': True, 'answer': 'The known issue is that the `/export` endpoint can time out under heavy load.'}
```

## 08 create_agent with HITL middleware

`HumanInTheLoopMiddleware` pauses a `create_agent` agent before its
`send_refund` tool runs. The application shows the pending call to a reviewer,
and `LangGraphHumanInTheLoopBridge` records the approval request and the
decision as feedback about the tool call. The agent then resumes with the
reviewer's decision. The model is a scripted fake, so this example runs
offline with no credentials.

```python title="examples/08_agent_hitl_middleware.py"
--8<-- "examples/08_agent_hitl_middleware.py"
```

```text title="Output"
Agent paused; approval request 5428963e-9123-442b-ab82-ad726af28b54 is received
Decision recorded: resolved, resolution={'response': 'approve', 'approved': True}
Tool ran after approval: Refund sent for order 1042
Agent resumed and finished: The refund for order 1042 was sent.
```
