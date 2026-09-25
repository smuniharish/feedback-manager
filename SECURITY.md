# Security Policy

## Supported versions

`feedback-manager` is currently in initial development (`0.x`). Security
fixes are made against the latest released `0.x` version on the `main`
branch; there is no long-term-support branch yet.

| Version | Supported          |
| ------- | ------------------ |
| 0.x     | :white_check_mark:  |

## Reporting a vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

Instead, report privately using one of the following:

- GitHub's [private vulnerability reporting](https://github.com/samamuniharish/feedback-manager/security/advisories/new)
  for this repository, or
- Email the maintainer directly at samamuniharish@gmail.com with a
  description of the issue, affected versions, and reproduction steps.

You should receive an acknowledgement within a reasonable timeframe. Please
give the maintainer a reasonable opportunity to investigate and address the
issue before any public disclosure.

## Scope

`feedback-manager` is a library embedded into applications; it is not a
hosted service. In scope for security reports:

- Vulnerabilities in the library's own code (e.g. injection via
  `feedback_id`/`idempotency_key` handling, unsafe deserialization in
  `FeedbackSerializer` implementations shipped by this package,
  logic errors in lifecycle/idempotency enforcement that could allow
  unauthorized state transitions).
- Issues where the library could cause an application to unintentionally
  log, persist, or leak sensitive feedback payloads.

Out of scope:

- Vulnerabilities in `langchain`, `langgraph`, or `langgraph-xai`
  themselves — please report those upstream to the respective projects.
- Misuse of extension points by application code that supplies its own
  `FeedbackStore`, `FeedbackHandler`, or serializer implementations.

## Handling of sensitive feedback data

`feedback-manager` does not include a database, transport layer, or
authentication/authorization framework — see
[docs/architecture/SECURITY_MODEL.md](docs/architecture/SECURITY_MODEL.md)
for the extension points (redaction, metadata filtering, retention, access
control hooks) applications are expected to use for their own compliance
requirements. The library itself does not transmit feedback payloads over
the network and does not log raw payload contents by default.
