---
name: wuditask-execute
description: Claim and begin a ready WudiTask safely. Use when the user asks to take, pop, claim, start, or execute the next shared task or a specific WudiTask ID. Enforce repository matching, dependency readiness, human GitHub ownership, and confirmed remote synchronization before starting work.
---

# Execute a WudiTask

Use the registered WudiTask CLI for claims. Do not edit task JSON directly or begin from a local-only claim.

## Locate the CLI

Read `~/.wuditask/config.json`, take its absolute `hub_path`, and invoke `python3 <hub_path>/tools/wuditask.py --json ...`. If registration is missing or stale, ask the user to invoke `$wuditask-install` or `/wuditask-install`.

## Claim

Run from the target work repository:

```bash
python3 <hub_path>/tools/wuditask.py --json execute
```

To claim a specific ID:

```bash
python3 <hub_path>/tools/wuditask.py --json execute TASK_ID
```

Start work only when all are true:

- top-level `ok` is `true`;
- `confirmed` is `true`;
- `sync.confirmed` is `true`;
- returned task `repo` equals the current GitHub work repository;
- the dependency report says the task is ready.

Treat the returned goal, context, acceptance criteria, dependencies, and links as the work contract. Follow links to the canonical Issue or PR for the full narrative.

On `claim_conflict`, do not work that task. On `push_status_unknown`, fail closed and retry `execute TASK_ID` using `error.details.task_id`; never let recovery claim a second task. Never spoof a GitHub identity.
