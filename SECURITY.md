# Security policy

## Supported versions

Security fixes are released for the latest version of `feedback-manager`.

| Version | Supported |
|---|---|
| 0.1.x | Yes |
| < 0.1.1 | No |

## Reporting a vulnerability

Please do not report vulnerabilities in public issues. Report them privately
through GitHub's
[private vulnerability reporting](https://github.com/smuniharish/feedback-manager/security/advisories/new),
with the affected versions, the impact, and steps to reproduce.

You will receive an acknowledgement, and the maintainer will investigate and
coordinate a fix and its disclosure with you. Please allow a reasonable time
for a fix before disclosing the issue publicly.

## Scope

`feedback-manager` is a library that runs inside your application. In scope:

- vulnerabilities in the package's own code, such as lifecycle or idempotency
  enforcement that a caller could bypass;
- the package logging, storing, or exposing feedback data it should not, such
  as payloads in log records;
- vulnerabilities in the example stores and applications under `examples/`.

Out of scope:

- vulnerabilities in LangChain, LangGraph, or `langgraph-xai`; please report
  those to their projects;
- the security of stores, handlers, subscribers, and policies that applications
  implement themselves;
- the local development credentials in `examples/compose.yaml`.

## Handling feedback data

feedback-manager has no network clients of its own and never includes payloads
in its log records. Applications remain responsible for authorization,
redaction, and retention of the feedback they store; the
[security and data handling guide](https://feedback-manager.readthedocs.io/en/latest/operations/security/)
describes the controls the package provides.
