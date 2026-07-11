---
name: wuditask-list
description: List shared WudiTask queue entries without mutation. Use when the user asks to list, find, filter, count, or summarize open, archived, or all tasks, optionally restricted to a GitHub repository.
---

# List WudiTasks

Use only the registered WudiTask CLI's read-only `list` command.

## Locate the CLI

Read `~/.wuditask/config.json`, take its absolute `hub_path`, and invoke `python3 <hub_path>/tools/wuditask.py --json ...`. If registration is missing or stale, ask the user to invoke `$wuditask-install` or `/wuditask-install`.

## List tasks

Choose the scope requested by the user:

```bash
python3 <hub_path>/tools/wuditask.py --json list --scope open
python3 <hub_path>/tools/wuditask.py --json list --scope archive
python3 <hub_path>/tools/wuditask.py --json list --scope all --repo owner/name
```

Summarize only the fields relevant to the question. Preserve task IDs, repositories, ownership, priority, derived state, completion outcome, and canonical links exactly.

Do not use a mutating command for listing. Use `$wuditask-show` or `/wuditask-show` when the user asks for one task's full details, and `$wuditask-dep-check` or `/wuditask-dep-check` for blocker analysis.
