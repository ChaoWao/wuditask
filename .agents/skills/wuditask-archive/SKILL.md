---
name: wuditask-archive
description: Archive a WudiTask as done, failed, or cancelled. Use when canonical GitHub delivery is terminal and the user wants to preserve the shared outcome, source-defined acceptance evidence, and any participating agent runs.
---

# Archive a WudiTask

Use the registered CLI. Do not edit, move, or delete task JSON directly.
Ordinary outcomes remain archived; use `$wuditask-delete` only for an
explicitly identified erroneous archived record.

## Locate the CLI

Read `~/.wuditask/config.json`, take its absolute `tool_path`, and invoke
`python3 <tool_path>/tools/wuditask.py --json ...`.

## Verify the source contract

Before `done`:

1. Read the canonical Issue or pull request; it is the only narrative and
   acceptance contract.
2. Run or inspect every acceptance check described there.
3. Commit and push delivery through the execution repository's workflow.
4. Confirm the canonical Issue is completed or the canonical pull request is
   merged. An open or reopened Issue remains active.
5. Record concrete free-form evidence. If active agents exist, use the exact
   `run_id` returned by this agent's successful `execute`. If none exist, the
   authenticated task creator must omit `--run-id`.

For `failed` or `cancelled`, provide a concrete result. A cancelled
GitHub-backed task must be terminal as not planned; nonterminal or unavailable
delivery fails closed. These outcomes do not unblock downstream tasks.

Read the current task before choosing the authorization form:

- If any active agents exist, the authenticated caller must own one of those
  entries and pass its exact `--run-id`. The archive snapshots every active
  entry as a participant and clears the entire active set. A `done` caller on
  this path must also remain a live GitHub owner.
- If no active agents exist, all outcomes must omit `--run-id` and may be
  archived only by the task's authenticated `created_by` login. Participants
  are empty. For `done`, dependencies must be ready, GitHub delivery must be a
  fresh successful terminal state, and evidence must be non-empty. This path
  covers delivery completed outside WudiTask as well as unclaimed or released
  work. A stale run ID is rejected instead of being ignored.

## Archive

```bash
python3 <tool_path>/tools/wuditask.py --json archive TASK_ID \
  --run-id RUN_ID \
  --outcome done \
  --result "Validation implemented and verified" \
  --evidence "python3 -m unittest tests.test_upload: 12 passed" \
  --evidence "Merged pull request: https://github.com/acme/api/pull/88"
```

When the task has no active agents, its authenticated creator uses the same
command without `--run-id`:

```bash
python3 <tool_path>/tools/wuditask.py --json archive TASK_ID \
  --outcome done \
  --result "Delivery completed and verified" \
  --evidence "Merged pull request: https://github.com/acme/api/pull/88" \
  --evidence "GitHub checks: 16/16 passed"
```

Evidence is a repeatable list tied to the acceptance requirements in the
source, not a duplicated acceptance-evidence table in Hub data. For active
work, the matching active-agent entry authorizes the archive and completion
preserves participating `login` and `run_id` values. Without active agents,
the authenticated creator authorizes any matching terminal outcome and the
participant list is empty; successful `done` still requires concrete evidence.

Report completion only when `ok=true`, `confirmed=true`, and
`sync.confirmed=true`. Treat insufficient evidence, a run mismatch, incomplete
delivery, or unavailable GitHub state as a hard stop.
