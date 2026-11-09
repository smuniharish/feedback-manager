# Install in your agent

Each skill is one directory with `SKILL.md` at its root: `feedback-manager`,
`feedback-manager-learning` and `feedback-manager-extend`. Every agent that
supports the [Agent Skills format](https://agentskills.io/specification) reads
the same directories, so there is no separate copy per agent.

## With the skills CLI

The [skills CLI](https://github.com/vercel-labs/skills) finds the three skills
in the repository, detects the agents in your project, and installs the skills
into each agent you choose. It needs Node.js 22.20 or later.

```bash
npx skills add smuniharish/feedback-manager                                    # choose interactively
npx skills add smuniharish/feedback-manager --skill feedback-manager           # only the core skill
npx skills add smuniharish/feedback-manager --skill feedback-manager-learning  # only the learning skill
npx skills add smuniharish/feedback-manager --skill feedback-manager-extend    # only the extension skill
```

| Option | Effect |
| --- | --- |
| `-g` | Install for every project instead of the current one |
| `-a claude-code` | Install for one agent; see the CLI's [agent list](https://github.com/vercel-labs/skills/blob/main/docs/agents.md) |

## Manually

Copy each complete skill directory, including `SKILL.md`, `references/`,
`scripts/` and `assets/`, into one of the locations for your agent. The
[`feedback-manager`](https://github.com/smuniharish/feedback-manager/tree/master/feedback-manager-skills/skills/feedback-manager),
[`feedback-manager-learning`](https://github.com/smuniharish/feedback-manager/tree/master/feedback-manager-skills/skills/feedback-manager-learning)
and [`feedback-manager-extend`](https://github.com/smuniharish/feedback-manager/tree/master/feedback-manager-skills/skills/feedback-manager-extend)
directories are in the feedback-manager repository. Their `evals/` folders only
test the skills, so you can leave them out.

=== "Claude Code"

    | Scope | Destination |
    | --- | --- |
    | Current repository | `.claude/skills/<skill>/` |
    | All local projects | `~/.claude/skills/<skill>/` |

    Restart Claude Code. It selects a skill when the task matches, or invoke
    it with `/feedback-manager`. See
    [Claude Code Skills](https://code.claude.com/docs/en/skills).

=== "Codex"

    | Scope | Destination |
    | --- | --- |
    | Current repository | `.agents/skills/<skill>/` |
    | All local projects | `~/.agents/skills/<skill>/` |

    Codex detects new skills automatically; restart it if a skill doesn't
    appear. Invoke one with `$feedback-manager`, or list skills with
    `/skills`. See [Codex Skills](https://learn.chatgpt.com/docs/build-skills).

=== "Cursor"

    | Scope | Destination |
    | --- | --- |
    | Current repository | `.agents/skills/<skill>/` or `.cursor/skills/<skill>/` |
    | All local projects | `~/.agents/skills/<skill>/` or `~/.cursor/skills/<skill>/` |

    Restart Cursor. In Agent chat, type `/` and select the skill, or let
    Cursor activate it from its description. See
    [Cursor Agent Skills](https://cursor.com/docs/skills).

=== "GitHub Copilot"

    | Scope | Destination |
    | --- | --- |
    | Current repository | `.agents/skills/<skill>/`, `.github/skills/<skill>/` or `.claude/skills/<skill>/` |
    | All local projects | `~/.agents/skills/<skill>/` or `~/.copilot/skills/<skill>/` |

    In Copilot CLI, start a new session or run `/skills reload`, then check
    with `/skills info feedback-manager`. Invoke a skill with
    `/feedback-manager`. See
    [Adding agent skills for GitHub Copilot CLI](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-skills).

=== "Other agents"

    Copy the skill folders into the skills directory your agent documents.
    The skills use only standard `SKILL.md` frontmatter, so they need no
    agent-specific adapter.

    If an agent needs a manifest or registry entry, add only a thin adapter
    that points to these directories. Don't copy the instructions.

## Verify

1. Each installed directory is named after its skill and contains `SKILL.md`.
2. Your agent lists the skills, if it has a skill list.
3. From your project's Python environment, the setup check passes:

    ```bash
    python <skills directory>/feedback-manager/scripts/verify_setup.py
    ```

    ```text
    PASS  Python 3.12.16 (3.12 or newer required)
    PASS  feedback-manager 0.1.2 (0.1.x expected by this skill)
    PASS  agxai 1.0.2 (>=1.0.2,<2 required)
    PASS  pydantic 2.14.3 (>=2.14.3,<3 required)
    PASS  structlog 26.1.0 (>=26.1.0,<27 required)
    PASS  langchain-core 1.7.6 (>=1.7.6,<2 required)
    PASS  langgraph 1.2.20 (>=1.2.20,<2 required)
    INFO  optional integrations: lancedb 0.41.0, ladybug 0.22.1, opentelemetry-sdk 1.45.2
    PASS  submit stores, correlates by thread, and deduplicates retries
    PASS  lifecycle rejects illegal moves and records the resolution
    PASS  a custom agent's tool failure is recorded once and still propagates
    PASS  a tool timeout is recorded once and still propagates
    PASS  a human-in-the-loop request and decision are recorded around the interrupt
    PASS  feedback links to agxai provenance during and after the run
    All checks passed.
    ```

    Without the `feedback-manager[langchain]` extra, the LangChain and
    LangGraph checks are skipped and the core checks still run. Add
    `--langchain` to fail instead, or `--json` to print one JSON object.

4. If you installed `feedback-manager-learning`, the learning check passes:

    ```bash
    python <skills directory>/feedback-manager-learning/scripts/verify_learning.py
    ```

    The last line is `All checks passed.`

5. If you installed `feedback-manager-extend`, its store template passes the
   store contract:

    ```bash
    cd <skills directory>/feedback-manager-extend
    python scripts/check_component.py store_template:TableFeedbackStore --path assets
    ```

    The last line is `All 6 promises hold.`

## Update

Run `npx skills update`, or replace the installed folders with the latest
directories from the repository, then restart or reload your agent. Each
skill's `metadata.version` matches the feedback-manager release it describes.
