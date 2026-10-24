# Local main-agent workflow

Part 3 helps the current main agent organize one authorized goal: agree the
scope, split work by dependencies, run independent items, review their results,
and deliver a complete change. Use the existing `$wuditask` entry; users do not
need to manage attempts or learn another skill.

GitHub remains the business record. Personal coordination (Part 2, or S2)
identifies this user's designated run across machines. The local plan (S3)
records work inside that run. A child agent is neither a new business owner nor
a separate S2 claim.

## What is implemented

- A revisioned `plan.json`, protected by local locks and atomic writes. The
  manager owns plan changes; stale revisions are rejected.
- Dependency validation, scope/resource reservations, immutable assignments,
  candidate reports, explicit observations and manager review.
- Native Codex handoffs and supervised Claude/Codex CLI processes. Process
  launch intent, output, exit and completion records survive the calling CLI.
- Polling and reconciliation of recorded attempts. An unknown launch is retained
  for investigation; it is not permission to start another writer.
- An offline HTML board generated from the same plan, with real artifact edges,
  expandable details, decision gates, blockers and current-head PR evidence.

These tools record and check execution facts. The main agent still judges the
actual design, diff, tests and authorization. A completed tool call, successful
process exit or child-agent “done” message does not accept a work item.

## Start with a complete delivery

Define the user scenario, default behavior, failure and shutdown behavior,
resource cost, acceptance evidence and allowed actions. Reuse approval already
present in the conversation. Bring a concrete internally reviewed proposal to
the user only when a material choice remains.

One delivery represents a meaningful feature or fix that can be accepted as a
whole. Its internal items may cover design, implementation, fixtures,
integration, independent review and tests. These items usually belong in one
PR. Split Issues by independently owned business responsibility and PRs by
independent user value, rather than by the number of agents.

The plan separates three dependency kinds:

| Kind | Required fact |
| --- | --- |
| `artifact` | The named work item's result was accepted by the manager. |
| `decision` | The named concrete proposal was approved. |
| `merged` | The named delivery was verified merged; baseline verification is required by default. An edge may also pin `merge_commit`. |

Cancellation never satisfies an artifact dependency. A changed input or
proposal requires affected results and successors to be reconsidered. Work on
independent authorized branches can continue while a decision is pending.

## Bind the plan to the right execution

For a GitHub task, set `task_ref` to the canonical anchor URL in the journal. First follow the existing acquire/check/takeover workflow and
bind the plan to its S2 session journal. Check that right again before dispatch,
on resume and before controlled external writes. Losing the right stops new
work; it does not kill old processes or undo a submitted remote operation.

Local design and authorized WudiTask maintenance can use `--local-reason`
instead. This records why a GitHub claim does not apply; it does not create
cross-machine coordination or bypass a real GitHub task's S2 claim.

Commands below are operator examples for the main agent. `TOOL` is the
registered tool's `tools/wuditask.py`; `PLAN` is a private local directory. Read
`work show` after a mutation and use the returned revision for the next one.

```bash
python3 "$TOOL" work init --plan "$PLAN" --spec spec.json --session session.json
# For a genuinely local maintenance/design goal, use this alternative:
python3 "$TOOL" work init --plan "$PLAN" --spec spec.json \
  --local-reason 'Authorized local tool maintenance; no business task claim'
python3 "$TOOL" work show --plan "$PLAN"
```

A minimal local spec uses stable logical item names:

```json
{
  "goal": "Make cancellation safe through shutdown",
  "task_ref": "local:cancellation-maintenance",
  "agreement": {
    "scope": "Cancellation and shutdown behavior of the local runner",
    "acceptance": "Cancellation releases the writer only after confirmed exit"
  },
  "deliveries": {"runner": {"title": "Complete cancellation lifecycle"}},
  "decisions": {},
  "items": {
    "implementation": {
      "title": "Implement and verify cancellation",
      "acceptance": "Exit and cancellation regression cases pass",
      "delivery": "runner",
      "access": "write",
      "write_scope": ["wuditask", "tests"],
      "resources": [],
      "depends_on": []
    }
  }
}
```

Record an actual authorization reference through `work approve`; do not invent
approval to make a node ready. Keep approval of the goal separate from a new
proposal decision, authorization to publish, and the user's decision to merge.

## Dispatch and review

Before every first or rework dispatch, read applicable repository instructions,
latest main and all potentially overlapping open PRs, including drafts. Inspect
relevant diffs and active local writers. Bind the disposition to the exact
prompt and baseline. A failed remote check leaves the baseline unknown; pursue
safe investigation while withholding potentially overlapping implementation.

`preflight.json` records `decision: "proceed"`, the exact prompt's
`prompt_sha256`, `main_evidence`, `open_pr_disposition` and `observed_at`.
Evidence should identify the main SHA, complete PR snapshot and treatment of
each overlap. Recheck before launch and before modification/publication; a
changed prompt or remote baseline requires another substantive review.

```bash
python3 "$TOOL" work prepare --plan "$PLAN" --revision "$REVISION" \
  --item implementation --attempt implementation-01 --backend codex-native \
  --workspace "$WORKSPACE" --prompt prompt.md --preflight preflight.json
```

The assignment includes its workspace, permitted files/actions, fixed inputs,
acceptance, resources and result location. Each scope has one current writer.
Independent worktrees prevent file collisions but do not remove logical or
hardware-resource conflicts. Tests must read a fixed commit or artifact while
writers work elsewhere. Follow the original author for rework where possible;
each rework execution gets a new attempt.

For `codex-native`, the current host's native spawn/message/wait tools perform
the actual call. Persist the handoff before calling, then save the exact
returned handle using `work observe`. If the host response is unknown,
reconcile that attempt before any retry. The CLI does not manufacture a native
child or assume its identity remains usable after restart.

For `claude` and optional CLI `codex`, `work launch` uses the prepared attempt.
The supervisor invokes Claude with a fixed session ID and JSON output, or
Codex with JSONL output; it keeps separate stdout/stderr and durable completion
records. It checks process identity rather than trusting a reusable PID.
Launcher availability, provider setup and permissions must be checked locally;
no permission-bypass flag is implied. If provider setup is a shell function,
pass `work launch --launcher /path/to/verified-executable-wrapper`; the wrapper
loads existing configuration, and credentials stay outside task files.

For rework, `work prepare --continue-attempt PREVIOUS` reuses a reviewed,
exited Claude session or native author handle for the same item/workspace.
Claude uses `--resume`, native uses the host followup API. CLI Codex continuation
is not supported; start a new attempt with an explicit context handoff instead.

A worker report is immutable candidate evidence, for example:

```json
{
  "run_id": "copy-from-assignment",
  "item_id": "implementation",
  "attempt_id": "implementation-01",
  "outcome": "candidate_ready",
  "summary": "Cancellation regression passes at the recorded commit",
  "head_commit": "exact-tested-commit",
  "evidence": [{"command": "python3 -m unittest tests.test_runner", "exit_code": 0}],
  "limitations": []
}
```

Use `failed`, `blocked` or `cancelled` when appropriate. An executor's own
`candidate` output is only a process-level observation; it does not substitute
for the task's `candidate_ready` report or manager acceptance.

```bash
python3 "$TOOL" work report --plan "$PLAN" \
  --attempt implementation-01 --result result.json
python3 "$TOOL" work observe --plan "$PLAN" --revision "$REVISION" \
  --attempt implementation-01 --state exited --handle "$HANDLE" \
  --reference 'Confirmed this attempt exited; saved host observation'
# Read the new revision before the next plan mutation.
python3 "$TOOL" work review --plan "$PLAN" --revision "$NEXT_REVISION" \
  --attempt implementation-01 --verdict accepted \
  --reference 'Reviewed actual diff and fixed-commit regression evidence'
```

The manager must inspect identity, baseline, real artifacts, scope, tests and
limitations before accepting. Confirm `exited` or `not_started` before review
releases the reservation. A report does not prove the writer has stopped.
`needs_rework`, `blocked` and `cancelled` preserve a truthful outcome. An old
attempt cannot overwrite the current one or unlock successors.

## Complete the PR and verify the baseline

Review the integrated result and all required tests on the exact current head.
Read all PR feedback: inline threads, review summaries and conversation
comments. Track each disposition, and send replies or resolve threads only
within the granted communication scope. Re-read remote results after an
external write; ambiguous push/PR/retry responses require reconciliation.

A delivery becomes `ready_for_merge` only with accepted required items and both
`review_evidence` and `ci_evidence` on its current `head`, each containing
`status: "passed"`, a concrete `reference` and `observed_at`. A head change
invalidates old readiness. Check actual CI jobs/steps and tested inputs; a green
documentation job is not evidence for the required runtime tests.

The user controls business PR merge. Authorized direct tool-maintenance updates
still follow the maintenance workflow. After merge, record the actual merged
head and merge commit, then verify the required baseline separately.
`merged` and `baseline_verified` are distinct. Continue only authorized
successors; neither a merge nor an accepted item closes the parent Issue by
itself. Further code changes use the applicable PR/review/CI workflow.

## Resume, wake and inspect

```bash
python3 "$TOOL" work poll --plan "$PLAN"
python3 "$TOOL" work show --plan "$PLAN"
python3 "$TOOL" work board --plan "$PLAN"
```

`poll` reads pending candidate/completion events and ready items. `reconcile`
checks recorded subprocesses and repairs recoverable missing completion
records. Interrupted preparations are recovered as unknown attempts with their
reservations intact. `poll` separates late historical evidence from pending
current work. Neither command accepts results. `watch` provides bounded observation;
it is not a second autonomous manager. See each command's `--help` for attempt,
revision and watch options.

A configured wake command can request that the host resume the existing main
session. Use it only after verifying the target identity and actual host
behavior. A successful enqueue/command receipt does not prove the manager
resumed or reviewed anything. Busy-session, waiting-session and ended-turn wake
are separate capabilities; do not claim ended-turn continuation without its
own real test. Without a configured and verified mechanism, say **manual resume
required** and give the user the existing task's continuation entry.

On resume, check rights and latest instructions, reconcile unknown launches and
external writes, inspect pending results, review them, then dispatch all
resource-safe ready work. Repeat until only external waits remain. No ready
nodes can mean running, blocked, awaiting decision or awaiting merge; it does
not mean complete. Keep unchanged monitoring quiet.

For a takeover, obtain the new S2 execution first and explicitly adopt the
plan. Verify accessible artifacts and current remote state. If an old writer's
exit is uncertain, use an isolated workspace; do not demand a stop receipt for
S2 takeover or overwrite the old directory. Old results remain candidate
history, not authority to update the new run.

Plan and attempt files are private local execution records. Keep large logs,
credentials and unpublished artifacts out of public GitHub/Pages. A
cross-machine checkpoint must reference accessible artifacts and fingerprints;
a path that exists only on the previous machine is insufficient.

## Verification

See [acceptance evidence](main-agent-acceptance.md) for the automated regressions,
real native/Claude exercise and unverified host-lifecycle boundary.

After adopting an accessible plan, `work reuse --plan PLAN --item ITEM
--reference 'current-scope review evidence' --revision N` can explicitly carry
forward a previously accepted result. It verifies the original exit, manager
verdict, identities and immutable result files; current dependencies and
approval must still hold. Missing source artifacts require recovering the
artifacts or doing the work again, not silently accepting an old status.

Evidence reuse currently requires directly reviewed source work. A reused
snapshot cannot itself be the next reuse source; retain the original reviewed
plan and artifacts for subsequent handoffs.
