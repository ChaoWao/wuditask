---
name: wuditask-dep-check
description: Inspect WudiTask dependencies, blockers, and readiness. Use when the user asks whether one or all tasks are ready, why a task is blocked, what another repository must finish, or whether dependency evidence is sufficient.
---

# Check WudiTask Dependencies

This workflow is read-only.

## Locate the CLI

Read `~/.wuditask/config.json`, take its absolute `tool_path`, and invoke `python3 <tool_path>/tools/wuditask.py --json ...`. The CLI reads the task Hub remote and branch from the same config. If registration is missing or stale, ask the user to invoke `$wuditask-install` or `/wuditask-install`.

## Inspect readiness

For one task:

```bash
python3 <tool_path>/tools/wuditask.py --json dep-check TASK_ID
```

For all open tasks:

```bash
python3 <tool_path>/tools/wuditask.py --json dep-check
```

Explain expanded dependency repositories, goals, acceptance criteria, outcomes, and evidence. Treat a dependency as ready only when it is archived with `outcome=done` and every acceptance criterion has complete passing evidence.

The command also reports live delivery for inspected tasks. GitHub merge or
Issue closure alone never satisfies a WudiTask dependency; it indicates that
verification and archive may now proceed.

Missing, open, failed, cancelled, incomplete, or cyclic dependencies block execution. Do not bypass blockers or mutate task files while answering a readiness question.
