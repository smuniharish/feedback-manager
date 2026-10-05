# Agents

Two examples with real agents and a real chat model: a LangChain agent that
uses an MCP server's tools, and a `deepagents` agent whose file edits need a
person's approval.

## Prerequisites

```bash
uv sync --group examples
```

Both examples build their model with LangChain's `init_chat_model`, so any
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

## 08 deepagents with human approval

A `deepagents` agent maintains release notes. Its `edit_file` tool requires
approval (`interrupt_on`), and the agent is instrumented by `langgraph-xai`.
When the agent proposes an edit, `HumanInTheLoopBridge` records the approval
request, linked to the proposed tool call and to the paused run's
provenance, and then the reviewer's decision. The agent edits a temporary copy
of `examples/mcp_workspace`, so the repository is never modified.

```python title="examples/08_agent_deepagents_hitl.py"
--8<-- "examples/08_agent_deepagents_hitl.py"
```

```text title="Output"
The agent proposes edit_file({'file_path': '/release_notes.md', 'old_string': '- The `/export` endpoint can time out under heavy load.', 'new_string': '- The `/export` endpoint was fixed in v0.4.0.'})
Approval request a2b945e5-111a-445f-8100-581d1f29b03d (received)
  linked to: langgraph-xai run 905fd076-bad6-408f-a251-956517261e36 (interrupted): 9 node execution(s), 2 tool execution(s)
Decision recorded: resolved, resolution={'response': {'decisions': [{'type': 'approve'}]}, 'approved': True}
Edited file:
# Release notes

## v0.3.0

- Added a PostgreSQL feedback store example.
- Added a Streamlit feedback capture UI.

### Known issues

- The `/export` endpoint was fixed in v0.4.0.
```
