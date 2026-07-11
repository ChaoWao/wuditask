---
name: wuditask-release
description: Return a claimed WudiTask to the shared queue. Use when the current human owner asks to release, unclaim, put back, or stop owning a task without archiving it, including when work is waiting for a decision or was claimed in the wrong repository.
---

# Release a WudiTask

Use the registered WudiTask CLI. Never clear owner or claim fields manually.

## Locate the CLI

Read `~/.wuditask/config.json`, take its absolute `hub_path`, and invoke `python3 <hub_path>/tools/wuditask.py --json ...`. If registration is missing or stale, ask the user to invoke `$wuditask-install` or `/wuditask-install`.

## Release

Confirm the current human owner intends to return the task to the queue and record a concrete reason:

```bash
python3 <hub_path>/tools/wuditask.py --json release TASK_ID \
  --reason "Waiting for product decision"
```

Release is confirmed only when `ok=true`, `confirmed=true`, and `sync.confirmed=true`. Stop on `owner_mismatch`; never spoof a GitHub identity or release another person's task.
