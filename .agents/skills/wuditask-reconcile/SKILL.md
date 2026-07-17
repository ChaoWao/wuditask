---
name: wuditask-reconcile
description: Compare WudiTask coordination state with the canonical GitHub Issue or pull request without mutating either system. Use when the user asks whether queue ownership, delivery progress, or completion status has drifted.
---

# Reconcile WudiTask and GitHub

Use only the registered WudiTask CLI's read-only `reconcile` command. It reads
the Hub from the installed configuration and fetches canonical GitHub delivery
state live; it never rewrites task JSON, assignees, Issues, or pull requests.

## Locate the CLI

Read `~/.wuditask/config.json`, take its absolute `tool_path`, and invoke:

```bash
python3 <tool_path>/tools/wuditask.py --json reconcile [TASK_ID]
```

Omit the task ID to inspect every open task. Report both dimensions rather
than collapsing them into one status:

- WudiTask coordination: dependencies, blockers, and execution claim;
- GitHub delivery: Issue state and assignees, closing pull requests, reviews,
  checks, merge state, and whether WudiTask verification is still required.
- Archived drift: `done` still maps to completed delivery and `cancelled` still
  maps to GitHub `NOT_PLANNED`, rather than a reopened or newly completed source.

Treat `delivery_unavailable` as unknown, not as unassigned or ready. Use the
operation-specific skill for any follow-up mutation: execute/release for a
lease, archive for a verified outcome, and normal GitHub workflows for Issue or
pull-request changes.
