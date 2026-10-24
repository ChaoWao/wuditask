# Plan and execute within the current main-agent session

Use this reference when the user asks WudiTask to plan, implement, delegate,
continue or handle completed internal work. Keep the existing `$wuditask` entry.
The current host session is the main agent; do not launch another manager.
[Tool behavior and CLI examples](../../../../docs/main-agent-workflow.md).

## Establish the goal and authority

1. Read the latest user instructions, applicable repository guides and the
   existing agreement. Preserve user limits on devices, retries, publication
   and scope across context changes.
2. For a GitHub task, follow [coordination](coordinate.md) and bind the plan to
   its current S2 journal. Internal items do not acquire separate S2 claims.
   For local design or authorized tool work, use an explicit local reason;
   follow [maintenance](maintain.md) for tool updates. Local mode is not a
   substitute for coordinating a real GitHub business task.
3. Describe a complete user-visible delivery, its normal/error/shutdown
   behavior, scope, cost and acceptance. One complete PR may contain many
   internal items. Split business Issues by responsibility and deliveries by
   independently useful outcomes, never just to distribute work to agents.
4. Reuse existing approval. Review child proposals internally before asking
   the user to choose. A material architecture, behavior, resource-cost or
   authorization change gets a concrete question, recommendation, alternatives,
   affected items and proposal revision. Pause only the affected branch.
   Ordinary repairs, failed CI and completed children are manager work, not new
   user decisions. Approval of design does not imply merge or messaging rights.

## Build and persist the internal plan

Use `work init/show/approve/decision/item` to maintain a single `plan.json`.
Plan mutations require the observed revision; a rejected stale revision means
reload and reconcile, not overwrite. Workers only submit their candidate
results. Keep the plan outside the business repository when practical.

Describe each item by goal, acceptance, delivery, inputs, workspace, read/write
scope and resources. Distinguish artifact acceptance, specific decision
approval and verified merge dependencies. Accepted artifacts are not merged
baselines. Cancelled predecessors do not satisfy success dependencies.

Before first dispatch **and each rework dispatch**:

- Check current S2 rights when bound; inspect latest main, the complete relevant
  open/draft PR inventory, overlapping diffs and active writers. Failed reads
  leave the baseline unknown; continue safe investigation without dispatching
  potentially duplicate implementation.
- Bind the final prompt digest, exact main evidence, PR snapshot/dispositions
  and observation time in preflight. Recheck before actual launch, modification
  and publication. Changed inputs require another review, not merely replacing
  a recorded SHA.
- Assign one writer per scope. Independent workspaces still need logical
  integration and resource reservations. Tests and independent review read a
  fixed commit/artifact, not a directory another writer is editing.
- Give the worker the exact cwd, baseline, approval, allowed files/actions,
  validation limits, result path and failure-reporting contract. External
  writes require explicit delegated scope. Do not move another worktree's
  shared branch ref or borrow an unrelated idle session.

## Dispatch using the available backend

Use `work prepare` to reserve the item and persist its immutable assignment
before the external call. Backend names are `codex-native`, `claude` and
`codex`. A successful preparation is not proof that a worker started.

For native Codex, use the host's actual spawn/message/wait capabilities and
save its handle with `work observe`. Record intent/unknown state before an
ambiguous call; if its response is lost, find the original attempt before
retrying. Use native notifications while active, plus durable reports for
recovery. If the child has not saved its output, save the returned candidate
honestly; do not claim the child wrote a file it did not create.

For Claude or CLI Codex, use `work launch` on the prepared attempt. The local
supervisor records launch intent, process identity, separate logs and terminal
evidence. A wrapper/provider must be verified locally; never copy credentials
into prompts/logs or add permission-bypass flags. The default subprocess path
uses a fixed Claude session ID. Use `--launcher` only for a verified executable
wrapper loading the existing provider configuration. Do not guess a previous session or concurrently
resume one that may still be running.

For rework prefer the original author and workspace where safe, but create a
new attempt. `work prepare --continue-attempt ID` preserves an exited, reviewed
Claude/native author for the same item and workspace; CLI Codex requires an
explicit new-session handoff. Send feedback to an active writer rather than starting a second
writer. Track pending/delivered/acknowledged feedback in handoff evidence.
Before context compression or backend changes, preserve approved design,
current artifacts, running operations, remaining review, test results,
permissions and next actions. A successful message send is not proof the
feedback was applied.

## Process results in the same working turn

1. Recheck authority and new user instructions.
2. Use `work poll/reconcile` and actual host observations to resolve unknown
   launches, outstanding processes, incomplete publications and pending events.
   Unknown is a real state. Never blindly repeat spawn, push, PR creation or a
   CI rerun after an ambiguous response.
3. Save candidate reports through `work report`. A worker's `candidate_ready`
   is not acceptance; process `candidate`, exit code zero and “Completed” are
   weaker observations still. Failures and missing reports need investigation.
4. Confirm the attempt exited or never started with `work observe` before
   releasing its writer reservation. Review actual identity, input baseline,
   diff/artifact, scope, fixed-input tests and limitations. Use `work review`
   with `accepted`, `needs_rework`, `blocked` or `cancelled` and concrete
   evidence. Late attempts remain history and cannot replace the current one.
5. Integrate accepted work, refresh dependencies, and dispatch all independent
   authorized ready items whose scopes/resources are available. Fix integration
   failures before calling the delivery complete.
6. Update the board, handle PR state and reread pending events. Continue while
   work is actionable. Otherwise checkpoint the precise wait: running,
   blocked, decision, merge or full baseline verification.

Distinguish source inspection, actual build artifacts, tests, CI and device
experiments. Record exact inputs, commands and limits. A later pass does not
prove the original root cause is fixed. Preserve applicable retry limits
across new attempts/heads; rerun only when justified and authorized.

## Deliver, observe merge, continue

Read every inline thread, review summary and conversation comment. Record its
disposition and who owns any authorized reply/resolve; reread the remote after
publication. Preserve others' latest Issue/PR body edits. Ordinary unchanged
polls do not warrant comments or empty commits.

`work delivery` records evidence; the manager must obtain and verify it.
Ready requires all required items accepted and passing review plus CI tied to
the exact current head. Inspect the required workflow/job/attempt and relevant
steps, including failures during cleanup. Head changes withdraw old Ready.
Report a complete feature, current head, verification and limitations when
asking the user to merge. Follow repository commit policy without imposing a
universal one-commit rule.

After an actual merge observation, verify the target, merged head, merge
commit and required baseline. Record `merged` separately from
`baseline_verified`. Continue only already-authorized successors. Do not close
a parent goal or Issue solely because one PR merged. Further code changes
follow the applicable review/CI/publication workflow.

## Recovery and truthful wake behavior

The board is a local snapshot, not live GitHub. Render with `work board`; keep
pending decisions separate from PRs ready to merge, and retain observation
ages and stale evidence. Completed-node collapse must not hide dependencies.

Use bounded `work watch` or the host's available wait mechanism while active.
A wake command is optional and may target only the verified current session.
Its accepted receipt means the command/queue accepted a request; it does not
prove the manager resumed. Verify busy, waiting and ended-turn behavior
separately before claiming support. Without verified automatic resume, report
**manual resume required**, save the plan location/checkpoint and explain how
to continue this task. Stay quiet while monitoring finds no meaningful change.

For explicit takeover, obtain new S2 rights, then `work adopt` with actual
artifact/provenance evidence. Use `work reuse` only after checking that an old
accepted artifact still satisfies the current contract; source files and their
identity/digests must be accessible. No stop receipt is required for S2 takeover. If
old writer exit remains uncertain, isolate the new workspace and keep the old
candidate artifacts intact. Never reuse old-run authority or silently accept
its late results. Cross-machine handoff must include accessible artifact
locations and fingerprints; local paths alone are insufficient.

Only export business milestones and approved summaries to GitHub. Private
logs, complete event history and credentials remain local/private. Tool
commands are mechanisms, not proof of authorization, correctness or a running
autonomous background manager.
