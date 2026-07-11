---
name: wuditask
description: Operate and explain a shared WudiTask GitHub-backed task queue from any work repository. Use when a user asks how to use WudiTask or invokes help, asks an agent to add or record work, pick/pop/claim/execute the next task, inspect task dependencies or readiness, archive completed/failed/cancelled work with acceptance evidence, release a claimed task, or inspect shared task state. Enforce human GitHub ownership, ordinary-push confirmation, and the repository's Python CLI contract.
---

# WudiTask

Use the registered WudiTask Python CLI for every task mutation. Do not edit task JSON directly.

## Locate the CLI

1. Read `~/.wuditask/config.json`.
2. Take the absolute `hub_path`.
3. Invoke `python3 <hub_path>/tools/wuditask.py --json ...`.
4. If config is missing or the path no longer exists, stop and ask the user to invoke `$wuditask-install` (Codex) or `/wuditask-install` (Claude).

Keep `--json` before the subcommand. The CLI itself obtains the human owner from `gh api user` for remote writes.

## Help and onboarding

When invoked as `/wuditask help [topic]`, `$wuditask help [topic]`, or when the user asks how WudiTask works:

1. Run `python3 <hub_path>/tools/wuditask.py --json help [topic]`.
2. Explain the returned workflow and commands in the user's language.
3. Show the matching Codex, Claude, and CLI invocation forms.
4. Mention that install uses symlinks, so updates in the same clone do not require reinstall.
5. Do not execute a mutating task command.

Supported topics are `workflow`, `add`, `execute`, `dep-check`, `archive`, `release`, `list`, `show`, and `install`. With no topic, present the four-step add/execute/dep-check/archive workflow first, followed by a compact command reference.

## Choose the operation

- Help, usage, or onboarding request: use `help`.
- New work request or “record this”: use `add`.
- “Take/pop/start the next task”: use `execute`.
- Dependency/readiness question: use `dep-check`.
- Completed, failed, or cancelled work: use `archive`.
- Return work to the queue: use `release`.
- State inspection: use `list` or `show`.

## Add

Collect before calling the CLI:

- concise title;
- target GitHub repository in `owner/name` form (normally current origin);
- concrete goal;
- relevant context and constraints;
- at least one observable acceptance criterion;
- a verification method for each criterion;
- priority, dependency task IDs, and links when known.

Do not invent acceptance criteria when the user's intent is ambiguous. Call add; if it returns `insufficient_task_spec`, ask the questions in `error.details.questions`, then retry.

```bash
python3 HUB/tools/wuditask.py --json add \
  --title "Harden upload validation" \
  --goal "Reject malformed uploads before object storage" \
  --context "Preserve the public API" \
  --accept "Malformed files return HTTP 400" \
  --verify "command::python3 -m unittest tests.test_upload" \
  --priority P1
```

Dependencies must already exist as WudiTask IDs. Add dependency tasks first instead of embedding free-form cross-repository descriptions.

## Execute

Run from the work repository:

```bash
python3 HUB/tools/wuditask.py --json execute
```

Or claim a specific ID:

```bash
python3 HUB/tools/wuditask.py --json execute TASK_ID
```

Start code work only when all are true:

- top-level `ok` is true;
- `confirmed` is true;
- `sync.confirmed` is true;
- returned task `repo` equals the current work repository.

Then use the returned goal, context, acceptance criteria, dependencies, and links as the work contract. A non-fast-forward push is retried by the CLI. On `claim_conflict`, do not work that task. On `push_status_unknown`, fail closed; take `error.details.task_id` and retry `execute TASK_ID` so recovery cannot claim a second task.

## Check dependencies

```bash
python3 HUB/tools/wuditask.py --json dep-check TASK_ID
```

Read expanded dependency repositories, goals, acceptance criteria, outcomes, and evidence. Treat only archived `done` dependencies with complete passing evidence as ready. Missing, open, failed, cancelled, incomplete, or cyclic dependencies block execution.

## Archive

Before archiving done:

1. Run every verification in the work repository.
2. Commit and push the work according to that repository's process.
3. Record specific evidence for every acceptance criterion.
4. Recheck that the claimed task and repo match.

```bash
python3 HUB/tools/wuditask.py --json archive TASK_ID \
  --outcome done \
  --result "Validation implemented and tests pass" \
  --evidence "AC-1=python3 -m unittest tests.test_upload: 12 passed"
```

Do not summarize “all tests pass” without naming the command or observable result. For `failed` or `cancelled`, provide a concrete result/reason; these outcomes intentionally do not unblock downstream tasks.

## Release

Use only when the current human owner should return the task to the queue:

```bash
python3 HUB/tools/wuditask.py --json release TASK_ID --reason "Waiting for product decision"
```

Never clear owner/claim manually.

## Safety

- Never force-push the Task Hub.
- Never begin work from a local-only claim.
- Never archive done without criterion-level evidence.
- Never delete archived tasks.
- Never spoof a GitHub identity for remote writes.
- Ask the user when task context or acceptance remains insufficient.

Read [references/protocol.md](references/protocol.md) when handling an unfamiliar CLI error or constructing structured task specs.
