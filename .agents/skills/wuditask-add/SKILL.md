---
name: wuditask-add
description: Add or record a fully specified item in the shared WudiTask queue. Use when the user asks to create, add, enqueue, or remember work. Prefer an existing GitHub Issue or PR as the canonical description when the work has a clear owning repository; create an Issue there when needed, and use WudiTask fields as a concise execution contract.
---

# Add a WudiTask

Use the registered WudiTask CLI for the task mutation. Do not edit task JSON directly.

## Locate the CLI

1. Read `~/.wuditask/config.json` and take its absolute `hub_path`.
2. If it is missing or stale, ask the user to invoke `$wuditask-install` or `/wuditask-install`.
3. Invoke `python3 <hub_path>/tools/wuditask.py --json ...`.

The CLI obtains the human owner from `gh api user` for remote writes.

## Build the execution contract

Collect:

- a concise title;
- target execution repository;
- concrete goal;
- only the context and constraints needed to execute;
- at least one observable acceptance criterion;
- a verification method for each criterion;
- priority and dependency task IDs.

Keep `goal`, `context`, and acceptance criteria concise. They are the executable contract, not a copy of the full Issue or PR body. Do not invent acceptance criteria when the user's intent is ambiguous.

Dependencies must already exist as WudiTask IDs. Add dependency tasks first instead of embedding free-form cross-repository descriptions.

Resolve every ambiguity before creating an Issue or mutating WudiTask. Do not leave an incomplete or orphaned Issue when the execution contract is not yet sufficient.

## Establish the canonical description

After the narrative and execution contract are complete:

1. If the work already has a matching open PR, use that PR as the canonical narrative.
2. Otherwise search the target repository for a matching open Issue and reuse it when it describes the same work.
3. If no match exists, inspect that repository's Issue templates and create an Issue in that repository containing the complete motivation, scope, constraints, and acceptance intent.
4. Put the canonical Issue or PR URL in the WudiTask `links` field.

Do not create an Issue in the WudiTask hub to describe work owned by another repository. Do not create an empty PR merely to hold a description. If the repository is a suitable canonical home but Issue creation fails because of authentication, permissions, validation, or network errors, stop and report the failure; do not silently fall back to text.

Only when no suitable GitHub repository exists for the narrative, keep the complete description in the WudiTask text fields, record the reason for having no Issue or PR link in `context`, and report it to the user. WudiTask schema v1 still requires an execution repository; ask the user for it rather than inventing one.

## Add the task

```bash
python3 <hub_path>/tools/wuditask.py --json add \
  --repo acme/api \
  --title "Harden upload validation" \
  --goal "Reject malformed uploads before object storage" \
  --context "Preserve the public API" \
  --accept "Malformed files return HTTP 400" \
  --verify "command::python3 -m unittest tests.test_upload" \
  --link "https://github.com/acme/api/issues/42" \
  --priority P1
```

If the CLI returns `insufficient_task_spec`, ask the questions in `error.details.questions`, then retry. Report the task ID only when `ok=true`, `confirmed=true`, and `sync.confirmed=true`.

Read [../wuditask/references/protocol.md](../wuditask/references/protocol.md) for large JSON specs or unfamiliar CLI errors.
