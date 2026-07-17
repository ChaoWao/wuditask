# WudiTask workflow

WudiTask separates durable delivery ownership from short-lived agent execution. A canonical GitHub Issue or pull request describes the work and reports delivery progress. The Task Hub records cross-repository dependencies, archived verification evidence, and the agents that are currently executing work.

## Two sources of truth

- GitHub owns the narrative and delivery state. Issue assignees and closing-linked pull-request authors are the owners; an ordinary timeline mention does not create an owner, and a closed, unmerged pull request no longer contributes one.
- The Hub owns coordination state. It groups `active_agents` by GitHub login, but an active agent never becomes a second task owner.
- GitHub assignment is not atomic. It is useful for responsibility and discovery, but assignment alone never authorizes an agent to start.

## 1. Assign on GitHub

Start with the canonical Issue or pull request in the repository where the work will be delivered. Assign a responsible GitHub user when that is already known. An unassigned item remains available for an eligible user to adopt through execute.

For automatic selection, execute first chooses an idle task assigned to the current GitHub login. If none is available, it chooses an unowned task. Work owned by somebody else is never silently adopted.

An explicit task ID records user intent to join that task. If the current login is not yet an owner, execute first adds it as a co-assignee without removing existing owners, confirms that separate GitHub update, and only then starts its Hub run.

## 2. Execute atomically

Run execute from the repository that will receive the change:

```bash
wuditask execute [TASK_ID]
```

Execute checks the repository, dependency readiness, canonical delivery state, and current GitHub owners. It then starts an agent by updating the Hub with an ordinary, non-force push. That Hub push is the atomic boundary: do not start work until the remote confirms the new active agent.

Assignment and Hub coordination cannot be one cross-repository transaction. Execute therefore refreshes GitHub and Hub state around the push and fails closed when ownership or coordination changed concurrently.

## 3. Check the work

Use the single read-only check workflow before starting, while delivering, and before archiving:

```bash
wuditask check [TASK_ID]
```

Check reports dependency readiness, GitHub owners, active agents, pull requests, reviews, checks, and whether acceptance verification is still required. A merged pull request or completed Issue advances delivery, but downstream dependencies remain blocked until verification evidence is archived as done in WudiTask.

## Finish or stop

Archive a verified result with evidence when delivery is complete. A done archive always requires the caller's matching active `run_id`. Failed or cancelled work uses the same guard while agents are active and clears every active entry; when none remain, only the authenticated task creator may archive the explicit terminal result, and the command must omit the run ID. Stale run IDs are rejected instead of ignored. This lets unclaimed or already released terminal work finish its lifecycle without weakening active-run protection.

Release stops only the matching login and `run_id`: it does not unassign the Issue, change pull-request ownership, or let a stale agent stop a newer run under the same login.

To install the tools and agent skills on a new machine, continue to [Join us](install.html).
