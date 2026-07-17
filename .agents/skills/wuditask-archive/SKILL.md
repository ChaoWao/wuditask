---
name: wuditask-archive
description: Archive a WudiTask as done, failed, or cancelled. Use when the user asks to complete, close, finish, fail, cancel, or archive shared work. Require criterion-level verification evidence for done outcomes and a concrete reason for failed or cancelled outcomes.
---

# Archive a WudiTask

Use the registered WudiTask CLI. Do not edit, move, or delete task JSON directly.

## Locate the CLI

Read `~/.wuditask/config.json`, take its absolute `tool_path`, and invoke `python3 <tool_path>/tools/wuditask.py --json ...`. The CLI reads the task Hub remote and branch from the same config. If registration is missing or stale, ask the user to invoke `$wuditask-install` or `/wuditask-install`.

## Prepare the outcome

Before archiving `done`:

1. Recheck that the claimed task and repository match.
2. Run every acceptance verification in the work repository.
3. Commit and push the implementation according to that repository's process.
4. Record specific evidence for every acceptance criterion.
5. Confirm the canonical GitHub Issue is currently completed (normally because
   a closing PR merged), or the canonical PR itself is merged. An open or
   reopened Issue remains active even if it retains a historical merged closing
   PR. GitHub completion enters verification; it does not automatically archive
   WudiTask.

Do not reduce evidence to “all tests pass.” Name the command, result, URL, file, or observable fact.

For `failed` or `cancelled`, provide a concrete result or reason. These outcomes
intentionally do not unblock downstream tasks. An unclaimed task may be
archived directly with either terminal outcome even when its dependencies are
blocked; the ordinary Hub push is the atomic race point. A task claimed by
another human still cannot be archived. For GitHub-backed `cancelled`, close
the canonical delivery as `NOT_PLANNED`. A `failed` outcome also permits a
completed delivery whose WudiTask acceptance verification failed; active or
unavailable delivery is not terminal.

## Archive

```bash
python3 <tool_path>/tools/wuditask.py --json archive TASK_ID \
  --outcome done \
  --result "Validation implemented and tests pass" \
  --evidence "AC-1=python3 -m unittest tests.test_upload: 12 passed"
```

Completion is confirmed only when `ok=true`, `confirmed=true`, and `sync.confirmed=true`. On `insufficient_archive_evidence`, run or check the missing criteria and retry with criterion-level evidence. Never archive a lease held by another human identity.

An Issue closed as `NOT_PLANNED` must not be archived `done`; use `cancelled`
with a concrete reason. A GitHub-backed `done` archive fails closed when live
delivery cannot be read.
