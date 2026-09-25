---
description: Production-grade feedback infrastructure for LangChain/LangGraph applications.
---

# Agent Skills

`feedback-manager` publishes one portable Agent Skill that teaches coding
agents how to integrate, configure, debug, test, and extend the existing
`feedback-manager` library. The skill is documentation and procedural
guidance; it is not a Python runtime component and does not change how
`feedback-manager` is installed.

| Component | Location |
| --- | --- |
| feedback-manager Python runtime | [`src/feedback_manager/`](https://github.com/smuniharish/feedback-manager/tree/master/src/feedback_manager) |
| Canonical Agent Skill | [`feedback-manager-skills/skills/feedback-manager/`](https://github.com/smuniharish/feedback-manager/tree/master/feedback-manager-skills/skills/feedback-manager) |
| Canonical instructions | [`SKILL.md`](https://github.com/smuniharish/feedback-manager/blob/master/feedback-manager-skills/skills/feedback-manager/SKILL.md) |

The skill follows the [Agent Skills specification](https://agentskills.io/specification)
and contains the required `name` and `description` frontmatter. There is no
separate Claude, Codex, Cursor, or Copilot copy of the skill.

## Install from skills.sh

The [skills CLI](https://www.skills.sh/docs/cli) installs skills from a
GitHub source. Install the feedback-manager skill directory directly:

```
npx skills add https://github.com/smuniharish/feedback-manager/tree/master/feedback-manager-skills/skills/feedback-manager
```

This is the portable installation route. Follow the CLI's current target
selection prompts, then verify that it placed the `feedback-manager` folder
in the target agent's supported skills directory. The shorthand
`npx skills add feedback-manager-skills` is **not** a valid source
identifier.

## Install manually

First obtain the canonical skill directory from the
[repository](https://github.com/smuniharish/feedback-manager/tree/master/feedback-manager-skills/skills/feedback-manager).
Copy the complete `feedback-manager` directory, including `SKILL.md` and
`references/`, into one of the host-specific locations below. Do not copy
only `SKILL.md`, because it links to the bundled references.

### Claude Code

Claude Code discovers standalone skills in:

| Scope | Destination |
| --- | --- |
| Current repository | `.claude/skills/feedback-manager/` |
| All local projects | `~/.claude/skills/feedback-manager/` |

Start or restart Claude Code after copying the directory. Claude can select
the skill when its description matches the task, or you can invoke it with
`/feedback-manager`. See [Claude Code Skills](https://code.claude.com/docs/en/skills).

`feedback-manager` does **not** currently ship a Claude plugin manifest or
marketplace package. Do not use
`claude plugin install feedback-manager-skills`. A plugin should be added
only if `feedback-manager` later needs to distribute multiple
Claude-specific components; the standalone skill is sufficient today.

### Codex

Codex discovers repository skills by scanning `.agents/skills` from the
working directory to the repository root. Copy the directory to:

| Scope | Destination |
| --- | --- |
| Current repository | `.agents/skills/feedback-manager/` |
| All local projects | `~/.agents/skills/feedback-manager/` |

Codex detects changes automatically; restart it if the skill does not
appear. Invoke it explicitly with `$feedback-manager` or use `/skills` to
inspect available skills. See
[ChatGPT and Codex Skills](https://learn.chatgpt.com/docs/build-skills).

### Cursor

Cursor supports the standard `.agents/skills` locations, which makes the
Codex layout above portable. It also supports Cursor-specific locations:

| Scope | Destination |
| --- | --- |
| Current repository | `.agents/skills/feedback-manager/` or `.cursor/skills/feedback-manager/` |
| All local projects | `~/.agents/skills/feedback-manager/` or `~/.cursor/skills/feedback-manager/` |

Restart Cursor after copying the directory. In Agent chat, type `/` and
select `feedback-manager` to attach it to a message; Cursor can also
activate it from its description. See
[Cursor Agent Skills](https://cursor.com/docs/skills).

### GitHub Copilot

GitHub Copilot supports the standard `.agents/skills` layout, and also the
following project and personal locations:

| Scope | Destination |
| --- | --- |
| Current repository | `.agents/skills/feedback-manager/`, `.github/skills/feedback-manager/`, or `.claude/skills/feedback-manager/` |
| All local projects | `~/.agents/skills/feedback-manager/` or `~/.copilot/skills/feedback-manager/` |

For Copilot CLI, start a new session or run `/skills reload`, then verify
the skill with `/skills info feedback-manager`. You can explicitly request
it in a prompt using `/feedback-manager`. See
[Adding agent skills for GitHub Copilot CLI](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-skills).

### Other Agent Skills-compatible hosts

Use the host's documented skill directory and copy the complete
`feedback-manager` folder there. The canonical skill relies only on the
standard `SKILL.md` frontmatter and sibling `references/` directory, so it
does not require a host-specific adapter.

If a host needs package metadata, a registry entry, or a plugin manifest,
follow that host's current official documentation and add only a thin
adapter that points to this canonical skill. Do not duplicate the skill
instructions.

## Update and verify

To update a manual installation, replace the complete installed
`feedback-manager` folder with the latest directory from the repository,
then restart or reload the host.

After installation, confirm all of the following:

1. The directory name is `feedback-manager`.
2. `SKILL.md` and `references/` are present in that directory.
3. The host lists `feedback-manager` as an available skill, if it exposes a
   skill listing command or UI.
4. A task about feedback capture, HITL approvals, tool failures, evaluator
   scores, or provenance-linked feedback activates or can explicitly invoke
   the skill.

See the distribution's
[README](https://github.com/smuniharish/feedback-manager/blob/master/feedback-manager-skills/README.md)
and
[validation process](https://github.com/smuniharish/feedback-manager/blob/master/feedback-manager-skills/validation/README.md)
for maintenance details.
