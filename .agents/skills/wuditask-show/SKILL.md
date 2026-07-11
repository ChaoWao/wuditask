---
name: wuditask-show
description: Show one WudiTask and its derived state without mutation. Use when the user provides or asks about a specific WudiTask ID and wants its repository, goal, context, acceptance criteria, owner, dependencies, links, or completion details.
---

# Show a WudiTask

Use only the registered WudiTask CLI's read-only `show` command.

## Locate the CLI

Read `~/.wuditask/config.json`, take its absolute `hub_path`, and invoke `python3 <hub_path>/tools/wuditask.py --json ...`. If registration is missing or stale, ask the user to invoke `$wuditask-install` or `/wuditask-install`.

## Show one task

```bash
python3 <hub_path>/tools/wuditask.py --json show TASK_ID
```

Present the task fields relevant to the user's question. Preserve the task ID, repository, ownership, derived state, goal, context, acceptance criteria, dependencies, links, and completion evidence exactly.

Do not use a mutating command for inspection. Use `$wuditask-list` or `/wuditask-list` for queue-wide queries, and `$wuditask-dep-check` or `/wuditask-dep-check` for expanded blocker analysis.
