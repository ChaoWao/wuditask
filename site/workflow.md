## 1. GitHub defines the work

Issue/PR holds the goal, design, progress, acceptance and native relationships. The business DAG reads GitHub directly. Issue shows its owner; PR shows its author. No extra queue entry or Hub archive unlocks downstream work.

## 2. One skill routes the intent

Use `$wuditask` for upgrade, overview, start, explicit takeover, checkpoint or finish. Upgrade keeps the original tool and execution repositories and branches. Only this skill is installed.

## 3. Coordinate the current execution

Same-account machines use the same execution branch. Stable user ID plus task node ID identifies the record. Default acquire avoids duplicates; different accounts are independent. Explicit takeover replaces the observed old run without stop proof. It does not terminate processes or fence pending writes.

```bash
wuditask --json coord inspect ISSUE_URL
```

## 4. Implement and validate

The main agent aligns the goal and approach using existing agreements, plans internal work, delegates when authorized, validates results and delivers a PR. Use the local `work` plan and HTML board for the internal DAG, typed artifact/decision/merge gates and delegated attempts. Codex native handoffs and supervised Claude/Codex CLI calls save candidate results; only manager review accepts work. Current-head review and CI are required before Ready. Internal work is not added to the business DAG. Check execution rights before starting work and controlled external writes.

## 5. Save progress and finish

Save a checkpoint and finish the exact personal run. This never releases a successor and never marks an Issue completed. Update GitHub under the existing task authorization; business acceptance remains there. Old queue records are historical only.

## Continue after a child finishes

Read the durable inbox, verify actual exit and evidence, accept or rework the result, then dispatch authorized successors. Unknown launches are reconciled, never blindly repeated. Native host events or a configured and verified wake bridge can notify the manager; without such support, explicitly resume the task. No background scheduler is installed by default.

[Full task-internal workflow](https://github.com/ChaoWao/wuditask/blob/main/docs/main-agent-workflow.md) · [Unified skill](https://github.com/ChaoWao/wuditask/blob/main/.agents/skills/wuditask/SKILL.md)
