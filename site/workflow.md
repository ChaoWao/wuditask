## Two sources of truth

- GitHub owns the task contract and delivery state. A canonical Issue or pull request holds the goal, constraints and acceptance criteria. PR owners are the author plus assignees; Issue owners are assignees plus authors of valid closing-linked pull requests. Ordinary mentions and closed, unmerged pull requests do not create owners.
- The Hub owns coordination state. It stores only the source reference, priority, dependencies, creator, `active_agents` and archive result. An active agent never becomes a second delivery owner.
- GitHub assignment and Hub execution are separate transactions. Assignment is useful for responsibility and discovery, but it never authorizes work by itself.

## 1. Define and queue the task

Use an Issue or pull request in the repository where delivery happens. A pull request can be the task itself; do not create a wrapper Issue. Use a Hub fallback Issue only when the execution repository cannot host one. Put the complete narrative and acceptance criteria on that canonical source before adding it to WudiTask.

```bash
wuditask add --repo OWNER/REPO --source GITHUB_URL --priority P1
```

Add validates the live source and every dependency before an ordinary Hub push creates the minimal open entry. A task is ready only when every dependency has been archived with outcome done. Closing an Issue or merging a pull request does not unlock downstream tasks by itself.

## 2. Assign and select work

Assignment changes GitHub only. It is additive, so multiple people can own one task:

```bash
wuditask assign TASK_ID
wuditask assign TASK_ID --to OTHER_LOGIN
```

Automatic execute first looks in the current execution repository for a ready task owned by the authenticated login where that login has no active run. If none exists, it considers unowned ready work. It never silently adopts work owned only by other people.

An explicit task ID is the user's decision to join that task. If the current login is not an owner, execute first adds it as a co-assignee without removing existing owners, confirms that GitHub transaction, and then attempts the separate Hub start.

## 3. Cross the atomic Hub boundary

Run execute from the repository that will receive the change:

```bash
wuditask execute [TASK_ID]
```

Execute validates the execution repository, dependencies, non-terminal delivery and fresh owners. The same login may have at most one active run on a task, even across different machines or agents; different logins may execute the same task together.

After those checks, execute generates a new run ID and uses an ordinary, non-force Hub push to add the exact `{login, run_id}` entry. Only this Hub update is the atomic execution boundary. The agent must wait for `ok=true`, `confirmed=true`, `sync.confirmed=true` and `work_authorized=true` before starting work.

If the push fails or its status is unknown, do not work. The earlier GitHub assignment remains. Use check to inspect the exact run and retry only after the state is known. After a confirmed push, execute reads GitHub again; ownership or delivery drift triggers compensation for only the new run and still denies work.

## 4. Observe and deliver

Use the same read-only command before execute, during delivery and before archive:

```bash
wuditask check [TASK_ID]
```

Check reports dependency blockers and readiness, GitHub owners, Hub active-agent logins, pull requests, reviews, checks, terminal delivery and coordination drift. GitHub unavailability remains unknown rather than being treated as unowned or complete.

The implementation, tests, pull request and review all happen in the execution repository. Acceptance stays on the canonical Issue or pull request. Pages combines the validated Hub snapshot with live GitHub delivery for the Tasks and Dependency graph views, but it is read-only and never publishes run IDs.

## 5. Release, archive or unassign

Release stops only the authenticated login's exact run. It preserves GitHub owners, other active agents and the open task, so the same person can execute again later:

```bash
wuditask release TASK_ID --run-id RUN_ID --reason "Waiting for input"
```

A done archive requires terminal successful delivery, the caller's matching active run and concrete acceptance evidence. The archive commit moves the task out of open state, records the result and participants, clears all active agents, and unlocks downstream dependencies.

Failed or cancelled archives require an explicit terminal GitHub state and never satisfy dependencies. While agents are active, the caller must provide a matching run and the archive records every participant before clearing the set. With no active agents, only the authenticated task creator may archive the terminal failure or cancellation, and the command must omit a run ID.

Unassign changes GitHub only. It refuses to remove a login with an active run; release each exact run first. Authorship or a valid closing-linked pull request can keep someone in the owner set even after their assignee entry is removed.

To install the CLI and all agent skills on a new machine, continue to [Join us](install.html).
