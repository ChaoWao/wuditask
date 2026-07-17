---
name: wuditask-execute
description: Start one agent run on a ready WudiTask. Use when work should begin on a task assigned to the current GitHub login, on an unowned task, or on an explicitly selected task after confirmed co-assignment, dependency, repository, delivery, and remote Hub checks.
---

# Execute a WudiTask

Use the registered CLI. Execute starts Hub work only for a current owner. When
the selected task has no owners, it first self-assigns the authenticated login
on GitHub and confirms that separate mutation. Use `$wuditask-assign` for
standalone assignment or to assign an explicitly authorized different login.

## Locate the CLI

Read `~/.wuditask/config.json`, take its absolute `tool_path`, and invoke
`python3 <tool_path>/tools/wuditask.py --json ...`.

## Start an agent run

Run from the target work repository:

```bash
python3 <tool_path>/tools/wuditask.py --json execute [TASK_ID]
```

Start work only when all are true:

- `ok=true`, `confirmed=true`, and `sync.confirmed=true`;
- returned `repo` matches the current GitHub work repository;
- all WudiTask dependencies are complete;
- canonical delivery is live and executable;
- the task is assigned to the current login, or the explicit/available task can
  successfully self-assign the current login first;
- the response authorizes work and returns this run's `run_id`.

For a pull-request source, owners are its author and assignees. For an Issue,
owners are its assignees and authors of closing-linked delivery pull requests.
Ordinary timeline mentions do not create owners. Multiple
different owner logins may execute concurrently. With no explicit ID, execute
chooses an idle ready task assigned to the current login first, then an unowned
ready task. Automatic selection never adopts work owned only by somebody else.
An explicit task ID is user intent to join that task: execute first self-assigns
the current login as a co-owner when needed, without removing existing owners.
The same login has at most one active entry; each entry carries an opaque
`run_id` to prevent stale release or archive operations from affecting a newer
run.

Self-assignment and starting the Hub run are two confirmed transactions. If
self-assignment succeeds but the Hub operation later fails, leave the GitHub
assignment in place and report that no run started; do not pretend the two
systems committed atomically and do not roll the assignment back.

Keep the returned `run_id` for `$wuditask-release` or `$wuditask-archive`.
Every start is checked again after the ordinary Hub push. If ownership or
delivery changes, compensation removes only the just-created login/run pair
and work must not start. It never removes the earlier GitHub self-assignment.

Treat the canonical Issue or pull request as the complete goal, context, and
acceptance contract. Fail closed on owner, dependency, delivery, run conflict,
or push uncertainty. Never invent another login or begin from local-only Hub
state.
