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

The main agent aligns the goal and approach using existing agreements, plans internal work, delegates when authorized, validates results and delivers a PR. Internal execution DAG and subagents belong to Part 3, not the business DAG. Check execution rights before starting work and controlled external writes.

## 5. Save progress and finish

Save a checkpoint and finish the exact personal run. This never releases a successor and never marks an Issue completed. Update GitHub under the existing task authorization; business acceptance remains there. Old queue records are historical only.
