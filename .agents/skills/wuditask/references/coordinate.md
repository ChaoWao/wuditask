# Task coordination

## Start or inspect

Read the Issue/PR and align the goal/approach with the user as needed. Existing explicit
agreement is sufficient; coordination adds no repeated approval ritual. Issue/PR remains
the business record. Do not auto-assign, auto-close, create a WDT task or require archive.

- Inspection only: `--json coord inspect URL`. Report current execution and checkpoint.
- Starting: choose a fresh journal under `~/.wuditask/personal/journals/`, then run
  `--json coord acquire URL --session JOURNAL`. Save the path in your local task context.
- Retry unknown responses with the **same journal and command**. Do not start until
  acquire and its current-claim check succeed.
- For a PR closing Issues, use its agreed Issue anchor (`--anchor ISSUE_URL`) or start
  from that Issue. If several Issues represent different scopes, clarify the scope.
- On conflict, inspect and report the current environment/run/checkpoint. Do not
  automatically take over merely because a process is old or unavailable.

Before starting work and before controlled external writes, run
`--json coord check --session JOURNAL`. A stale/not-active result means stop new work and
subagent dispatch; report the replacement instead of reacquiring automatically. Request
cancellation of your own subagents when supported. Checks do not atomically fence arbitrary
writes or retract operations already sent.

An already-running agent may use acquire/check directly; it does not need to launch a
second copy of itself. `coord guard --session JOURNAL -- HOST_COMMAND` is optional for a
foreground host you are launching. It holds a local lock and permits one launch per journal;
it does not kill the old host after takeover or automatically end the execution record.

After a successful claim, hand implementation to the repository's task-development
skill or instructions. Include this journal in the handoff so the main agent can
check rights and save progress. Children share its task authority; WudiTask does
not prescribe their plan, board, command or result format. On resume, inspect the
repository's existing work and results before dispatching again.

## Continue here / take over

An explicit user request such as “在这里继续”, “接管”, or “旧机器不用管，继续做” authorizes
replacement. Do not ask for stop proof or ask the same permission again. If intent is only
“status” or an ambiguous “continue”, inspect the existing agreement before replacing work.

1. Inspect and capture the exact current `run_id`; read its checkpoint and available artifacts.
2. Choose a **new journal** and run:

   ```bash
   wuditask --json coord takeover URL --expected-run-id OLD_RUN \
     --reason 'the user’s explicit takeover instruction' --session NEW_JOURNAL
   ```

3. Reuse this exact command/journal on uncertain responses. If `takeover_conflict` reports
   a changed run, inspect and report it; do not loop by targeting each new successor.
4. Read accessible prior plans and results under the repository workflow; revalidate evidence and approval.
   Keep old live writers isolated. Verify code/workspace state before resuming. Best-effort notify/cancel the old execution
   through available authorized host controls, but do not make stop confirmation a gate.
   Explain that old processes may still finish pending writes. Never kill unrelated processes.

Takeover atomically marks the observed old run superseded and installs your new run.
It does not prove the old process stopped. Different users' records are not affected.

## Record progress, pause or finish

At meaningful milestones: `coord checkpoint --session JOURNAL --summary 'short progress or accessible reference'`.
Keep sensitive details local/private. This saves recovery context without publishing a GitHub comment.

When pausing, cancelling or completing the execution, stop dispatching new work, save the
remaining work/results, and run:

```bash
wuditask --json coord finish --session JOURNAL --outcome paused --summary 'handoff summary/reference'
```

Use `completed` or `cancelled` when appropriate. No JSON stop receipt is required. Repeat
an uncertain finish with the same outcome/summary. Finish only ends this execution record;
follow the user's existing permissions for Issue/PR updates and never equate it with Issue closure.
Stop your own new writes after finish. Note any known continuing background work in the checkpoint.

Strict verified handoff (`quiesce/release/recover` with stop evidence) remains optional when
specifically desired. Read [the protocol](../../../../docs/personal-coordination.md) for that
path or for storage/migration details; do not impose it on ordinary continuation.
