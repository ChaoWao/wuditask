---
name: wuditask-show
description: Show one WudiTask with its canonical GitHub source, live owners and delivery, Hub dependencies and active agent runs, or archived completion. Use for a detailed read-only task inspection.
---

# Show a WudiTask

Use only the registered CLI's read-only `show` command.

## Locate the CLI

Read `~/.wuditask/config.json`, take its absolute `tool_path`, and invoke
`python3 <tool_path>/tools/wuditask.py --json ...`.

## Show one task

```bash
python3 <tool_path>/tools/wuditask.py --json show TASK_ID
```

Present the canonical Issue or pull-request title, body/acceptance contract,
owners and delivery together with the Hub's execution repository, priority,
dependencies, active `login`/`run_id` entries, and completion when archived.
Do not describe an owner as active unless that login has an active-agent entry,
and do not describe an active agent as a GitHub assignee unless delivery says
so.

Use `$wuditask-list` for queue-wide queries and `$wuditask-check` for expanded
blockers and consistency observations. Do not mutate during inspection.
