---
name: wuditask-release
description: Stop one WudiTask agent run without changing GitHub ownership or archiving the task. Use when an executing agent pauses, abandons, or hands off its exact login/run_id entry.
---

# Release a WudiTask Agent Run

Use the registered CLI. Never edit `active_agents` directly.

## Locate the CLI

Read `~/.wuditask/config.json`, take its absolute `tool_path`, and invoke
`python3 <tool_path>/tools/wuditask.py --json ...`.

## Release the exact run

Use the `run_id` returned by execute and record a concrete reason:

```bash
python3 <tool_path>/tools/wuditask.py --json release TASK_ID \
  --run-id RUN_ID \
  --reason "Waiting for product decision"
```

Release removes only the current authenticated login with that exact
`run_id`. It never removes an Issue or pull-request assignee, never changes a
PR author, and never stops another login's run. A stale run ID must fail rather
than remove a newer run by the same login.

GitHub ownership and execution are independent. Release must remain possible
when live delivery is unavailable or the login was externally unassigned; use
`$wuditask-unassign` separately when GitHub responsibility should also change.

Report success only when `ok=true`, `confirmed=true`, and
`sync.confirmed=true`. Stop on `agent_not_active`, run mismatch, or uncertain
push status; never spoof a login or reuse another agent's `run_id`.
