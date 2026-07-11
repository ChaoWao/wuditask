---
name: wuditask-archive
description: Archive a claimed WudiTask as done, failed, or cancelled. Use when the user asks to complete, close, finish, fail, cancel, or archive shared work. Require criterion-level verification evidence for done outcomes and a concrete reason for failed or cancelled outcomes.
---

# Archive a WudiTask

Use the registered WudiTask CLI. Do not edit, move, or delete task JSON directly.

## Locate the CLI

Read `~/.wuditask/config.json`, take its absolute `hub_path`, and invoke `python3 <hub_path>/tools/wuditask.py --json ...`. If registration is missing or stale, ask the user to invoke `$wuditask-install` or `/wuditask-install`.

## Prepare the outcome

Before archiving `done`:

1. Recheck that the claimed task and repository match.
2. Run every acceptance verification in the work repository.
3. Commit and push the implementation according to that repository's process.
4. Record specific evidence for every acceptance criterion.

Do not reduce evidence to “all tests pass.” Name the command, result, URL, file, or observable fact.

For `failed` or `cancelled`, provide a concrete result or reason. These outcomes intentionally do not unblock downstream tasks.

## Archive

```bash
python3 <hub_path>/tools/wuditask.py --json archive TASK_ID \
  --outcome done \
  --result "Validation implemented and tests pass" \
  --evidence "AC-1=python3 -m unittest tests.test_upload: 12 passed"
```

Completion is confirmed only when `ok=true`, `confirmed=true`, and `sync.confirmed=true`. On `insufficient_archive_evidence`, run or check the missing criteria and retry with criterion-level evidence. Never archive work owned by another human identity.

Read [../wuditask/references/protocol.md](../wuditask/references/protocol.md) for unfamiliar errors.
