# Example: `deepagents` + real MCP tool + HITL feedback capture

[`deepagents`](https://pypi.org/project/deepagents/) is a batteries-included agent harness built on LangGraph (planning, sub-agents, human-in-the-loop approval gates, its own filesystem tools). This example points deepagents' built-in filesystem tools at a real directory on disk via `FilesystemBackend`, loads the real Playwright MCP server as an additional tool, gates the destructive `edit_file` tool behind human approval, and uses `feedback-manager`'s `HumanInTheLoopBridge` to turn that pause into a durable feedback record -- exactly the "LangGraph interrupt -> FeedbackManager -> FeedbackEvent" flow the package is designed around. LangGraph still owns pausing/resuming; FeedbackManager only owns the record of *why* and *what was decided*.

Full source, embedded directly from `examples/08_agent_deepagents_mcp.py`:

```python title="examples/08_agent_deepagents_mcp.py"
--8<-- "examples/08_agent_deepagents_mcp.py"
```

## Real run: real pause, real approval, real file edit

```console
$ uv run python examples/08_agent_deepagents_mcp.py
Connecting to the real Playwright MCP server...
Loaded 25 real Playwright MCP tools (browser automation).
Gating deepagents' built-in 'edit_file' tool behind human approval.

Running deepagents agent with real LLM + real filesystem backend.
Task: In release_notes.md, change the line about the known issue so it reads that the issue was fixed in v0.4.0.

Agent paused for human approval of 1 tool call(s):
  - edit_file({'file_path': '.../mcp_workspace/release_notes.md', 'old_string': '...', 'new_string': '...'})

Feedback request recorded: id=47245621-2ab1-4511-b95e-946e469e1219 status=received
Human decision recorded: status=resolved

Agent resumed and finished:
Updated the known-issue line in `release_notes.md` to state that it was fixed in v0.4.0.

Final file contents on the real disk:
# Release Notes

## v0.3.0
- Added PostgresFeedbackStore example.
- Added Streamlit feedback capture UI.
- The known issue with the /export endpoint occasionally timing out under heavy load was fixed in v0.4.0.
```

The graph genuinely paused mid-execution (a real LangGraph interrupt, not a simulated one), the approval was recorded as a real `FeedbackEvent` moving through `received -> acknowledged -> handled -> resolved`, and the file on disk was actually modified only after that approval was granted.
