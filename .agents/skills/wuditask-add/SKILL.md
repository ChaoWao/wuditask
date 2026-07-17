---
name: wuditask-add
description: Add a GitHub-backed item to the shared WudiTask queue. Use when work needs queue priority or cross-repository dependencies after one canonical GitHub Issue or pull request already contains the complete narrative and acceptance requirements.
---

# Add a WudiTask

Use the registered CLI for the Hub mutation. Never edit task JSON directly.

## Locate the installation

Read `~/.wuditask/config.json`; use its absolute `tool_path` for the CLI and
derive a fallback Issue repository only from `hub_remote`. Do not infer the Hub
from the tool clone's origin.

## Establish the source first

Choose exactly one canonical GitHub source:

1. Reuse a matching pull request in the execution repository.
2. Reuse a matching Issue in the execution repository.
3. Inspect that repository's templates and create the Issue there.
4. If the execution repository cannot host the work, reuse or create an Issue
   in the configured Hub repository.

The Issue or pull request must contain the complete goal, scope, constraints,
dependencies, and independently verifiable acceptance requirements before the
WudiTask is added. A temporary authentication or network failure is not a
reason to create a text task. WudiTask has no text-source compatibility path.

`repo` identifies where work executes and may differ from `source.repo`. A Hub
Issue is still an ordinary canonical GitHub Issue; do not create an empty pull
request merely to hold a description.

## Add only coordination data

```bash
python3 <tool_path>/tools/wuditask.py --json add \
  --repo acme/api \
  --source https://github.com/acme/api/issues/42 \
  --priority P1 \
  --depends WDT-20260711T120000Z-A1B2C3
```

Omit `--depends` when there is no WudiTask dependency. The CLI verifies that
the source exists and is readable. Do not duplicate its title, body, or
acceptance requirements in task JSON.

Report the task ID only when `ok=true`, `confirmed=true`, and
`sync.confirmed=true`.
