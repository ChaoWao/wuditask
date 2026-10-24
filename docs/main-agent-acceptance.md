# Part 3 acceptance evidence

Validation date: 2026-10-24. Scope: WudiTask 0.9.0 local main-agent workflow.

## Automated verification

`python3 -m unittest discover -s tests -v`: **308 tests passed** before publication.
The suite includes existing business-graph, installation and personal-coordination
regressions as well as the new workflow cases.

| Area | Covered behavior |
| --- | --- |
| Plan | Typed DAG and cycles, decision gates, stale revision, current task/run binding, cancellation and merge dependencies |
| Reservations | Overlapping writes, shared resources, shared Git branch refs across worktrees, retained scopes until confirmed exit |
| Results | Candidate versus manager acceptance, immutable publication/digests, identity/path validation, failed executor veto, old/late reports |
| Recovery | Interrupted preparation before plan commit, recovered unknown attempt, retained reservation, missing result/completion, explicit adoption and direct-source evidence reuse |
| Delivery | Required items, current-head review/CI, invalidated Ready, immutable landed acceptance and separate baseline verification |
| Executors | Launch intent/idempotence, PID identity, failures, missing output, resume/wrapper argv, wake rejection/unknown/manual behavior |
| Board | Offline rendering, escaping, DAG interaction, actual awaiting-review state, merge-gate requirements and pinned commits |
| CLI | Native prepare/report/observe/review roundtrip, board output and structured stale-revision rejection |

Ruff checks pass for the new modules/tests. Unified skill validation passes.
Board tests include JavaScript syntax and interaction checks. Fault injection
and subprocess protocol tests use controlled local fixtures; they are not proof
of every external host or provider's behavior.

## Real agent exercise

One private local maintenance plan used two independent read-only items and a
join depending on their accepted artifacts:

1. A real native Codex author returned its result while the manager was active.
   The manager preserved the result, confirmed the completed turn and reviewed it.
   The join remained blocked while the other item was unaccepted.
2. Bare `claude` exited with an authentication error before model execution.
   The supervisor saved failure/exit/completion records. It was reviewed as
   needing rework, not counted as a successful model result.
3. The next Claude attempt used an executable wrapper around the machine's
   existing configured shell function. It returned a real model response and
   exit code zero. The wrapper contained no credentials; provider configuration
   remained in its existing location.
4. After the manager reviewed that response, the join became ready. A native
   followup inspected the two result sets. The manager received its completion
   through the host wait mechanism, then reviewed the join result.

Private transcripts, session IDs and machine paths are intentionally not
published. This exercise verifies native/Claude result collection, failure
handling, manager acceptance and fork/join progression. It does not simulate a
business PR merge or claim the software was developed entirely under its own
new coordinator.

## Boundaries

- Native completion was observed with an active manager and through host wait.
  **Ended-turn automatic resumption was not validated.** Without a configured,
  verified host wake bridge, continuation requires resuming the task manually.
- Wake-command return code zero means request acceptance, not that the manager
  resumed or accepted work. No cron job or autonomous manager is installed.
- Real GitHub PR/CI/merge transitions remain manager observations, backed by
  exact-head evidence. Their state guards are tested with controlled data;
  no artificial live PR was created for this exercise.
- Personal coordination checks designate the current run. They cannot retract
  already-submitted external operations or forcibly stop arbitrary writers.
- Evidence reuse currently requires the directly reviewed original source; chaining
  a reused snapshot into another reuse is rejected. Keep the original source
  accessible for further handoff.
- No performance improvement or model-quality guarantee is claimed.
