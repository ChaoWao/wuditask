---
name: wuditask-unassign
description: Remove a GitHub assignee from a WudiTask's canonical Issue or pull request without releasing agent runs. Use for the current login by default or another named login only with explicit user authorization.
---

# Remove GitHub Assignment

Use the registered CLI. Unassignment mutates only GitHub; it never removes a
Hub `active_agents` entry and never stops work implicitly.

## Locate the CLI

Read `~/.wuditask/config.json`, take its absolute `tool_path`, and invoke
`python3 <tool_path>/tools/wuditask.py --json ...`.

## Unassign

Remove the authenticated login's assignment:

```bash
python3 <tool_path>/tools/wuditask.py --json unassign TASK_ID
```

Remove another login only when the user explicitly names and authorizes it:

```bash
python3 <tool_path>/tools/wuditask.py --json unassign TASK_ID --from LOGIN
```

Never infer `--from` or remove every assignee. Inspect `check` first. The CLI
must refuse to unassign a login that still has an active run. Release each exact
login/run pair through `$wuditask-release` before retrying; explicit permission
to unassign another login does not bypass this active-run guard.

Removing an assignee cannot remove PR authorship. A PR author, or an Issue's
closing-linked PR author, may remain a live owner afterward; report that state
clearly.
Other assignees and all active-agent entries remain unchanged.

Report success only after the CLI refreshes delivery and confirms the requested
assignee removal. Treat permission, API, or confirmation uncertainty as a hard
stop.
