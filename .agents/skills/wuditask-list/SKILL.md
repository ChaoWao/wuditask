---
name: wuditask-list
description: List shared WudiTask entries without mutation. Use when the user asks to find, filter, count, or summarize open, archived, ready, or actively executed work across one or more repositories.
---

# List WudiTasks

Use only the registered CLI's read-only `list` command.

## Locate the CLI

Read `~/.wuditask/config.json`, take its absolute `tool_path`, and invoke
`python3 <tool_path>/tools/wuditask.py --json ...`.

## List tasks

```bash
python3 <tool_path>/tools/wuditask.py --json list --scope open
python3 <tool_path>/tools/wuditask.py --json list --scope archive
python3 <tool_path>/tools/wuditask.py --json list --scope all --repo owner/name
```

Summarize task IDs, execution repositories, priority, dependencies, live
GitHub owners, active-agent logins, completion outcome, and canonical source.
Show `run_id` only when it is needed to release or archive a specific run.
Treat unavailable delivery as unknown, never as unowned.

Use `$wuditask-show` for one full task and `$wuditask-check` for expanded
readiness and GitHub/Hub consistency. Do not mutate while listing.
