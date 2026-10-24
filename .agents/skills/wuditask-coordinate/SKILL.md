---
name: wuditask-coordinate
description: Coordinate your work on a GitHub Issue or PR across machines using WudiTask. Use when starting, inspecting, continuing elsewhere, taking over, pausing or finishing a tracked task. Keeps GitHub business ownership separate from the current personal execution; does not replace the task's implementation workflow.
---

# Coordinate a GitHub task

The user gives the task and intent; handle run IDs, session files and CLI calls yourself.
Use the authenticated GitHub account. Different people can share an execution repository;
records are independent by stable user ID and GitHub task node ID. This coordinates the
current designated execution, not arbitrary process writes.

## Find the tool and configure once

Use `wuditask` if registered, or read `~/.wuditask/config.json` for `tool_path` and invoke
`python3 <tool_path>/tools/wuditask.py`. When using this skill from an uninstalled clone,
resolve the repository root three levels above this SKILL.md directory.

If `~/.wuditask/personal/config.json` exists, use that authority. Never silently switch it
because a task is busy. Otherwise use the execution remote/branch already agreed with the
user or team (an existing install's `hub_remote` can supply the repository). If none is
known, ask only for the execution repository. All environments of an account must use the
same branch. Different accounts may share it. Do not create a new repository per person.

Run `coord setup --remote REMOTE --branch personal-execution` for the agreed fresh branch.
It creates/validates that execution branch, registers this environment and installs skills.
It leaves old branches and tasks untouched. An existing legacy branch is refused; choose
an agreed separate execution branch rather than migrating old tasks to get started.
Do not store credentials in URLs or checkpoint summaries. Shared public storage must not
contain private conversation, machine details or sensitive checkpoint content.

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
4. Verify code/workspace state before resuming. Best-effort notify/cancel the old execution
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
specifically desired. Read [the protocol](../../../docs/personal-coordination.md) for that
path or for storage/migration details; do not impose it on ordinary continuation.
