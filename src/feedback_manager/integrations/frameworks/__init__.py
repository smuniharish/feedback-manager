"""Agent-framework adapters, one subpackage per framework.

- ``langchain``: LangChain and LangGraph (``feedback-manager[langchain]``).
- ``custom``: a dependency-free adapter for custom Python agents and for
  frameworks without a dedicated adapter.

Importing this package imports no framework. To add a framework, such as
Agno or CrewAI, create ``frameworks/<name>/`` implementing the contracts in
`feedback_manager.integrations.base`, add an optional extra for its
dependencies, and run the shared adapter contract tests against it.
"""
