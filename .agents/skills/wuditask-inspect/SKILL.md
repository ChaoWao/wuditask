---
name: wuditask-inspect
description: Inspect shared WudiTask state without mutating it. Use when the user asks to list open or archived tasks, show a task by ID, filter tasks by repository, inspect ownership or outcomes, or summarize the current queue.
---

# Inspect WudiTask

This workflow is read-only.

## Locate the CLI

Read `~/.wuditask/config.json`, take its absolute `hub_path`, and invoke `python3 <hub_path>/tools/wuditask.py --json ...`. If registration is missing or stale, ask the user to invoke `$wuditask-install` or `/wuditask-install`.

## List tasks

```bash
python3 <hub_path>/tools/wuditask.py --json list --scope open
python3 <hub_path>/tools/wuditask.py --json list --scope archive
python3 <hub_path>/tools/wuditask.py --json list --scope all --repo owner/name
```

## Show one task

```bash
python3 <hub_path>/tools/wuditask.py --json show TASK_ID
```

Summarize only the fields relevant to the user's question. Preserve task IDs, repositories, ownership, derived state, dependency status, completion outcome, and canonical links exactly. Do not use a mutating command for inspection.

For a deeper blocker analysis, hand off to `$wuditask-dep-check` or `/wuditask-dep-check`.
