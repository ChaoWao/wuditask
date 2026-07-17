---
name: wuditask-assign
description: Assign a GitHub user to a WudiTask's canonical Issue or pull request without starting execution. Use when responsibility should be added for the current login or, with explicit user authorization, another named login.
---

# Assign GitHub Responsibility

Use the registered CLI. Assignment mutates only the canonical GitHub Issue or
pull request; it never adds a Hub `active_agents` entry.

## Locate the CLI

Read `~/.wuditask/config.json`, take its absolute `tool_path`, and invoke
`python3 <tool_path>/tools/wuditask.py --json ...`.

## Assign

Assign the authenticated GitHub login:

```bash
python3 <tool_path>/tools/wuditask.py --json assign TASK_ID
```

Assign another login only when the user explicitly names and authorizes it:

```bash
python3 <tool_path>/tools/wuditask.py --json assign TASK_ID --to LOGIN
```

Never infer `--to`, use it for convenience, or present it as impersonation.
GitHub applies repository permissions and source-specific assignment rules.
The CLI re-reads delivery and confirms the target appears among live owners.

For a PR source, owners are its author and assignees. For an Issue, owners are
its assignees and closing-linked PR authors. An ordinary timeline mention does
not create an owner. Assignment therefore may already be
satisfied through authorship.

Assignment does not mean execution has started. Use `$wuditask-execute` only
after dependencies are ready. Report the target login and refreshed owners;
stop on unavailable delivery, authorization failure, or an unconfirmed update.
