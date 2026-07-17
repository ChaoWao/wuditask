---
name: wuditask-delete
description: Delete one or more explicitly identified erroneous archived WudiTask records as a guarded atomic Hub update. Use only when the user asks to remove mistakenly created, duplicate, test, or otherwise invalid archived tasks; do not use for ordinary completion, cancellation, history cleanup, or sensitive-data erasure.
---

# Delete Archived WudiTasks

Use the registered WudiTask CLI. Never edit or remove task JSON directly.

## Confirm the deletion request

Require the user to explicitly identify every archived task to delete. Resolve
descriptions to exact IDs with the read-only `list --scope archive` and `show`
commands. Do not infer that an ordinary done, failed, or cancelled task should
be deleted; normal outcomes remain archived.

Record a concrete reason explaining why the records themselves are erroneous.
The current Hub tree keeps a durable receipt under `data/deletions/` with the
exact sorted batch IDs, reason, verified GitHub identity, and UTC timestamp.
Those task IDs are permanently reserved and cannot be recreated. The receipt
and original records remain in Git history; published Pages artifacts, clones,
and GitHub Issue/PR history are not erased, so never present this operation as
privacy or secret remediation.

## Delete one guarded batch

Read `~/.wuditask/config.json`, take its absolute `tool_path`, and invoke:

```bash
python3 <tool_path>/tools/wuditask.py --json delete \
  TASK_ID [TASK_ID ...] \
  --reason "These records were created by mistake."
```

Submit all requested IDs in one command. The CLI rejects the complete batch
before mutation when an ID is missing, still open, duplicated, malformed, or
referenced by any open or archived task outside the batch. Never bypass a
reverse dependency failure by editing files; ask the user whether the dependent
record should also be deleted or changed through its proper workflow.

The CLI does not close, reopen, assign, or otherwise mutate any canonical
GitHub source. Remote deletion uses one ordinary non-force Hub push and replays
all guards after concurrent changes. Its deterministic receipt ID is derived
from the sorted IDs, trimmed reason, and the actor's immutable GitHub numeric
ID. A different actor or reason is a different operation and cannot confirm
this request.

Delete requires the configured remote Hub. Never add `--local`: local files do
not provide a crash-safe multi-record commit boundary, so the CLI rejects that
mode.

Report completion only when `ok=true`, `confirmed=true`, and
`sync.confirmed=true`; verify the returned `deletion_receipt`, and name every
returned `deleted_task_ids` entry. If the push status is uncertain, retry the
same complete command. Idempotent retry and remote reconciliation require both
the matching deterministic receipt and every target task record to be absent;
absence alone is never confirmation.
