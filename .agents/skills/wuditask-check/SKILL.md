---
name: wuditask-check
description: Check WudiTask readiness and GitHub/Hub consistency without mutation. Use when the user asks about dependencies, blockers, owners, active agents, delivery progress, terminal archival work, or drift for one task or all current open and archived tasks.
---

# Check WudiTask State

Use only the registered CLI's read-only `check` command. It replaces the old
dependency-check and reconciliation workflows; those commands have no
compatibility aliases.

## Locate the CLI

Read `~/.wuditask/config.json`, take its absolute `tool_path`, and invoke:

```bash
python3 <tool_path>/tools/wuditask.py --json check [TASK_ID]
```

Omit the ID to inspect all current open and archived tasks.

## Report both sources of truth

Explain:

- Hub coordination: execution repository, priority, expanded dependencies,
  blockers, readiness, and each active agent's `login` and `run_id`;
- GitHub delivery: canonical Issue/PR state, live owners, linked pull requests,
  reviews, checks, and terminal state;
- consistency: active agents that are no longer owners, unavailable delivery,
  terminal open tasks needing archive, and archived outcome/source drift.

For a pull-request source, owners are the author and PR assignees. For an Issue,
owners are Issue assignees plus closing-linked pull-request authors; an ordinary
timeline mention does not create an owner. Multiple owners and
multiple active logins are normal. An owner without an active agent is not
executing; an active agent does not create GitHub ownership.

Treat unavailable delivery as unknown, not unowned or ready. Dependencies
unblock only after a WudiTask is archived `done`; a GitHub merge or closure
alone does not unblock them.

Check never rewrites task JSON or GitHub assignment. Use the operation-specific
assign, execute, release, unassign, or archive skill for any requested mutation.
