---
name: wuditask-execute
description: Claim and begin a ready WudiTask safely. Enforce execution-repository matching, dependencies, live canonical GitHub ownership, and a confirmed WudiTask lease before work starts.
---

# Execute a WudiTask

Use the registered WudiTask CLI for claims. Do not edit task JSON directly or begin from a local-only claim.

## Locate the CLI

Read `~/.wuditask/config.json`, take its absolute `tool_path`, and invoke `python3 <tool_path>/tools/wuditask.py --json ...`. The CLI reads the task Hub remote and branch from the same config. If registration is missing or stale, ask the user to invoke `$wuditask-install` or `/wuditask-install`.

## Claim

Run from the target work repository:

```bash
python3 <tool_path>/tools/wuditask.py --json execute
```

To claim a specific ID:

```bash
python3 <tool_path>/tools/wuditask.py --json execute TASK_ID
```

Start work only when all are true:

- top-level `ok` is `true`;
- `confirmed` is `true`;
- `sync.confirmed` is `true`;
- returned task `repo` equals the current GitHub work repository;
- the dependency report says the task is ready;
- live GitHub delivery is available and the current user is eligible;
- `work_authorized` is `true`.

Treat the returned goal, context, acceptance criteria, dependencies, and
`source` as the work contract. Follow the canonical source for the full
narrative. `links` are auxiliary only.

Every GitHub-backed claim is rechecked after the Hub push. If the Issue is
unassigned, execute first establishes the Hub lease, assigns the current
GitHub user, and then rechecks delivery. Assignment failure or a concurrent
owner causes token-guarded compensation; do not start work. If
another assignee or active closing-PR author owns delivery, respect
`delivery_owned_elsewhere`.

An explicit execute of a delivery-complete task may acquire the lease only to
perform acceptance verification and archive it. It returns
`work_authorized=false`; automatic execute skips such tasks. Local `--hub
--local` mode never assigns a real GitHub Issue and cannot claim an unassigned
GitHub-backed task for implementation.

On `claim_conflict`, GitHub delivery unavailability, reconciliation failure, or
`push_status_unknown`, fail closed. Never spoof a GitHub identity.
