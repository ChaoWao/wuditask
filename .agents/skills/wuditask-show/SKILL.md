---
name: wuditask-show
description: Show one WudiTask with separate coordination and live GitHub delivery state. Use for its execution repo, canonical source, goal, claim, dependencies, acceptance, or completion details.
---

# Show a WudiTask

Use only the registered WudiTask CLI's read-only `show` command.

## Locate the CLI

Read `~/.wuditask/config.json`, take its absolute `tool_path`, and invoke `python3 <tool_path>/tools/wuditask.py --json ...`. The CLI reads the task Hub remote and branch from the same config. If registration is missing or stale, ask the user to invoke `$wuditask-install` or `/wuditask-install`.

## Show one task

```bash
python3 <tool_path>/tools/wuditask.py --json show TASK_ID
```

Present the relevant task fields exactly. Distinguish the WudiTask execution
claim from GitHub assignees and closing-PR authors. The structured `source` is
canonical; `links` are supporting references.

Do not use a mutating command for inspection. Use `$wuditask-list` or `/wuditask-list` for queue-wide queries, and `$wuditask-dep-check` or `/wuditask-dep-check` for expanded blocker analysis.
