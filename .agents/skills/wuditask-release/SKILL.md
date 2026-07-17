---
name: wuditask-release
description: Return a claimed WudiTask execution lease to the shared queue without archiving it. Use when the current claim holder asks to release or stop work.
---

# Release a WudiTask

Use the registered WudiTask CLI. Never clear the claim field manually.

## Locate the CLI

Read `~/.wuditask/config.json`, take its absolute `tool_path`, and invoke `python3 <tool_path>/tools/wuditask.py --json ...`. The CLI reads the task Hub remote and branch from the same config. If registration is missing or stale, ask the user to invoke `$wuditask-install` or `/wuditask-install`.

## Release

Confirm the current human claim holder intends to return the lease and record a concrete reason:

```bash
python3 <tool_path>/tools/wuditask.py --json release TASK_ID \
  --reason "Waiting for product decision"
```

For a GitHub Issue source, release removes the current user's Issue assignment,
rechecks delivery, and then clears the Hub lease. It refuses to claim the task
is back in the queue while the current user owns an active closing PR. Any
GitHub/API or compensation uncertainty fails closed and leaves an actionable
reconciliation error. Other assignees are never removed.

Release is confirmed only when `ok=true`, `confirmed=true`, and
`sync.confirmed=true`. Stop on `claim_holder_mismatch`; never spoof a GitHub
identity or release another person's lease. Local `--hub --local` mode changes
only the explicit local Hub and never edits a real Issue assignment.
