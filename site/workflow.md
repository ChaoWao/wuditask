## 1. GitHub defines the work

Issue/PR holds the goal, design, progress, acceptance and native relationships. The business DAG reads GitHub directly. Issue shows its owner; PR shows its author. No extra queue entry or Hub archive unlocks downstream work.

## 2. One skill routes the intent

Use `$wuditask` for upgrade, overview, start, explicit takeover, checkpoint or finish. Upgrade keeps the original tool and execution repositories and branches. Only this skill is installed.

## 3. Coordinate the current execution

Same-account machines use the same execution branch. Stable user ID plus task node ID identifies the record. Default acquire avoids duplicates; different accounts are independent. Explicit takeover replaces the observed old run without stop proof. It does not terminate processes or fence pending writes.

```bash
wuditask --json coord inspect ISSUE_URL
```

## 4. Hand off to the repository workflow

After acquisition, read the target repository's task-development skill and current instructions. In simpler, use [develop-task](https://github.com/hw-native-sys/simpler/blob/main/.claude/skills/develop-task/SKILL.md) for design alignment, internal dependencies, boards, agent collaboration and reviewed delivery. The agent chooses the plan format and invocation methods for the current task and host. WudiTask does not run an internal scheduler or impose a plan schema.

Pass the personal execution journal into that workflow. Check execution rights before starting work and controlled external writes. Children share the main agent's authority rather than acquiring separate business tasks. Internal work is not added to the business DAG.

## 5. Save progress and finish

Save a checkpoint and finish the exact personal run. This never releases a successor and never marks an Issue completed. Update GitHub under the existing task authorization; business acceptance remains there. Old queue records are historical only.

## Continue through the same entry

Ask the chosen agent to use the wuditask skill. Codex and Claude Code use the installed links; OpenCode also reads their compatible global skill directories. WudiTask checks personal execution rights, then the repository workflow resumes its saved work and reviews available results. Verify actual host wake support rather than promising unattended continuation.

[Unified skill](https://github.com/ChaoWao/wuditask/blob/main/.agents/skills/wuditask/SKILL.md) · [Installation and client discovery](install.html)
