# Agent Skills

`feedback-manager` publishes one portable Agent Skill that teaches coding
agents how to integrate, configure, test, and debug `feedback-manager` in an
application. The skill is guidance for agents; it is not part of the Python
package and does not change how `feedback-manager` is installed.

| Component | Location |
| --- | --- |
| feedback-manager Python package | [`src/feedback_manager/`](https://github.com/smuniharish/feedback-manager/tree/master/src/feedback_manager) |
| The Agent Skill | [`feedback-manager-skills/skills/feedback-manager/`](https://github.com/smuniharish/feedback-manager/tree/master/feedback-manager-skills/skills/feedback-manager) |
| Its instructions | [`SKILL.md`](https://github.com/smuniharish/feedback-manager/blob/master/feedback-manager-skills/skills/feedback-manager/SKILL.md) |

## What the skill contains

The skill follows the [Agent Skills specification](https://agentskills.io/specification).
Agents read `SKILL.md` first and open the other files only when a task needs
them:

- **`SKILL.md`**: when to use the skill, the core workflow, and the rules an
  integration follows. Its frontmatter has `name`, `description`, `license`,
  `compatibility`, and `metadata.version`, the `feedback-manager` release it
  describes.
- **`references/API.md`**: public imports, methods, models, contracts, and
  errors.
- **`references/RECIPES.md`**: complete patterns for recording feedback,
  capturing failures, human-in-the-loop approvals, routing, provenance,
  storage, redaction, business rules, failure modes, expiry, and delivery.
- **`references/TROUBLESHOOTING.md`**: symptoms, causes, and fixes.
- **`scripts/verify_setup.py`**: an offline check that the project's
  environment has compatible versions, and that submission, the lifecycle,
  failure capture, human-in-the-loop, and provenance work.
- **`assets/test_feedback_integration.py`**: a pytest template for testing an
  application's feedback integration.

The repository validates the skill on every change: the reference validator,
link checks, the setup script, and the template run in its test suite. There
is no separate Claude, Codex, Cursor, or Copilot copy of the skill.

## Install from skills.sh

The [skills CLI](https://www.skills.sh/docs/cli) installs skills from a GitHub
source. Install the `feedback-manager` skill directory directly:

```bash
npx skills add https://github.com/smuniharish/feedback-manager/tree/master/feedback-manager-skills/skills/feedback-manager
```

Follow the CLI's target selection prompts, then verify that it placed the
`feedback-manager` folder in the target agent's skills directory.

## Install manually

Copy the complete `feedback-manager` directory from the
[repository](https://github.com/smuniharish/feedback-manager/tree/master/feedback-manager-skills/skills/feedback-manager),
including `SKILL.md`, into one of the host-specific locations below.

### Claude Code

| Scope | Destination |
| --- | --- |
| Current repository | `.claude/skills/feedback-manager/` |
| All local projects | `~/.claude/skills/feedback-manager/` |

Start or restart Claude Code after copying the directory. Claude can select
the skill when its description matches the task, or you can invoke it with
`/feedback-manager`. See [Claude Code Skills](https://code.claude.com/docs/en/skills).

### Codex

Codex discovers skills in `.agents/skills` from the working directory up to
the repository root:

| Scope | Destination |
| --- | --- |
| Current repository | `.agents/skills/feedback-manager/` |
| All local projects | `~/.agents/skills/feedback-manager/` |

Restart Codex if the skill does not appear. Invoke it explicitly with
`$feedback-manager`, or use `/skills` to list available skills. See
[ChatGPT and Codex Skills](https://learn.chatgpt.com/docs/build-skills).

### Cursor

| Scope | Destination |
| --- | --- |
| Current repository | `.agents/skills/feedback-manager/` or `.cursor/skills/feedback-manager/` |
| All local projects | `~/.agents/skills/feedback-manager/` or `~/.cursor/skills/feedback-manager/` |

Restart Cursor after copying the directory. In Agent chat, type `/` and select
`feedback-manager`; Cursor can also activate it from its description. See
[Cursor Agent Skills](https://cursor.com/docs/skills).

### GitHub Copilot

| Scope | Destination |
| --- | --- |
| Current repository | `.agents/skills/feedback-manager/`, `.github/skills/feedback-manager/`, or `.claude/skills/feedback-manager/` |
| All local projects | `~/.agents/skills/feedback-manager/` or `~/.copilot/skills/feedback-manager/` |

For Copilot CLI, start a new session or run `/skills reload`, then check the
skill with `/skills info feedback-manager`. See
[Adding agent skills for GitHub Copilot CLI](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-skills).

### Other hosts

Use the host's documented skills directory and copy the complete
`feedback-manager` folder there. The skill relies only on the standard
`SKILL.md` frontmatter, so it needs no host-specific adapter.

## Verify the installation

1. The directory is named `feedback-manager` and contains `SKILL.md`,
   `references/`, `scripts/`, and `assets/`.
2. The host lists `feedback-manager` as an available skill, if it has a skill
   listing.
3. From your project's Python environment, the setup check passes:

    ```bash
    python <skills directory>/feedback-manager/scripts/verify_setup.py
    ```

    ```text
    PASS  Python 3.12.14 (3.12 or newer required)
    PASS  feedback-manager 0.1.1 (0.1.x expected by this skill)
    PASS  langgraph 1.2.12 (>=1.2.12,<2 required)
    PASS  langgraph-xai 1.0.0 (>=1.0.0,<2 required)
    PASS  langchain-core 1.6.6 (>=1.6.6,<2 required)
    PASS  pydantic 2.13.5 (>=2.13.5,<3 required)
    PASS  structlog 26.1.0 (>=26.1.0,<27 required)
    PASS  submit stores, correlates by thread, and deduplicates retries
    PASS  lifecycle rejects illegal moves and records the resolution
    PASS  a tool timeout is recorded once and still propagates
    PASS  a human-in-the-loop request and decision are recorded around the interrupt
    PASS  feedback links to langgraph-xai provenance during and after the run
    All checks passed.
    ```

4. A task about capturing, routing, or resolving feedback, human-in-the-loop
   approvals, or failures in a LangChain or LangGraph application activates
   the skill, or you can invoke it explicitly.

To update a manual installation, replace the installed `feedback-manager`
folder with the latest one from the repository, then restart or reload the
host. See the distribution's
[README](https://github.com/smuniharish/feedback-manager/blob/master/feedback-manager-skills/README.md)
and [validation process](https://github.com/smuniharish/feedback-manager/blob/master/feedback-manager-skills/validation/README.md)
for maintenance details.
