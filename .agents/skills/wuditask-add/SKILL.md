---
name: wuditask-add
description: Add a fully specified item to the shared WudiTask queue. Use the owning repository's Issue or PR as the canonical source when possible, otherwise create a fallback Issue in the configured WudiTask Hub; text is the last resort.
---

# Add a WudiTask

Use the registered CLI for the queue mutation. Never edit task JSON directly.

## Locate the installation

Read `~/.wuditask/config.json`; use its absolute `tool_path` for the CLI and
derive the fallback Issue repository only from `hub_remote`. Do not infer the
Hub from the tool clone's origin.

## Build the execution contract

Collect a concise title, execution `repo`, concrete goal, necessary context,
priority, existing WudiTask dependencies, and independently verifiable
acceptance criteria. Resolve ambiguity before creating an Issue.

`repo` always identifies where the work executes. It is independent from the
repository holding the canonical `source`.

## Establish the canonical source

Choose exactly one source in this order:

1. Reuse a matching open PR in the execution repository.
2. Reuse a matching open Issue in the execution repository.
3. Inspect the execution repository's templates and create the Issue there.
4. If that repository cannot host the narrative because Issues are disabled,
   the actor lacks permission, or cross-repository work has no suitable single
   home, reuse or create a fallback Issue in the configured Hub repository.
5. Use an explained text source only when neither repository can host a
   GitHub Issue or PR. Do not turn a transient authentication or network error
   into a silent text fallback.

For a Hub fallback, use its `Fallback task` Issue form and record the target
execution repository, concrete fallback reason, complete narrative, scope,
acceptance intent, and dependencies. The WudiTask `source.repo` is then the Hub
while task `repo` remains the execution repository.

Do not create an empty PR merely to hold a description. `links` contains only
supporting references; it is not the canonical source.

## Add the task

Target-repository Issue:

```bash
python3 <tool_path>/tools/wuditask.py --json add \
  --repo acme/api \
  --source https://github.com/acme/api/issues/42 \
  --title "Harden upload validation" \
  --goal "Reject malformed uploads before object storage" \
  --context "Preserve the public API" \
  --accept "Malformed files return HTTP 400" \
  --verify "command::python3 -m unittest tests.test_upload" \
  --priority P1
```

Hub fallback Issue:

```bash
python3 <tool_path>/tools/wuditask.py --json add \
  --repo acme/api \
  --source https://github.com/acme/wuditask-hub/issues/42 \
  --source-fallback-reason "The execution repository has Issues disabled" \
  --title "Harden upload validation" \
  --goal "Reject malformed uploads before object storage" \
  --accept "Malformed files return HTTP 400"
```

Text-only fallback uses `--text-source-reason TEXT` instead of `--source`.

The CLI verifies that every GitHub source exists and is readable before the
Hub push. A cross-repository source is accepted only as a fallback Issue in the
configured Hub; an arbitrary third-party Issue or PR is rejected.

Report the task ID only when `ok=true`, `confirmed=true`, and
`sync.confirmed=true`.
