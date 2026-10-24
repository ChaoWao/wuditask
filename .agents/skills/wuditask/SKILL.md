---
name: wuditask
description: Use WudiTask for GitHub work overview, personal cross-machine task coordination, installation, upgrade, or tool maintenance. One entry for upgrading an old two-repository installation, starting a task, taking over here, saving progress and finishing. GitHub remains the business record.
---

# WudiTask

One skill: `wuditask`. Invoke `$wuditask` in Codex, `/wuditask` in Claude Code,
or ask OpenCode to use the wuditask skill. Route by the user's intent; handle CLI details,
session files and run IDs yourself. Reuse existing goal and authorization agreements.

## Locate

Use `~/.wuditask/config.json` → `tool_path`, then invoke
`python3 <tool_path>/tools/wuditask.py`. In an unregistered clone, resolve this skill's
repository root three levels above its directory. Do not create another repository or
execution branch to bypass existing work.

## Upgrade / install

For “upgrade”, “升级旧安装”, or first-time setup, follow [upgrade](references/upgrade.md).
This is the upgrade skill workflow within this same entry. It keeps the original two
repositories and original execution branch, installs only this skill and removes managed
legacy skill links. No separate queue registration or manual stop receipt is required.

## Overview

Read GitHub Issues/PRs or the configured Pages business DAG. Issue owner and PR author are
business facts; execution records are not a second business task list. Inspection never
claims work. Do not auto-assign, auto-close, or create a WDT task.

## Work on a task / continue here / pause / finish

Follow [coordination](references/coordinate.md). Different accounts may share the same
execution repository; all machines of one account use the same branch. Default acquire
prevents duplicate designated runs. Explicit user takeover replaces the observed run
without stop proof; it does not kill processes or retract pending external writes.
Check current rights before starting work and controlled external writes.

## Hand implementation to the repository

After acquisition, read the target repository's current agent instructions and
relevant task-development skill. In simpler, use
[develop-task](https://github.com/hw-native-sys/simpler/blob/main/.claude/skills/develop-task/SKILL.md)
from the current checkout. It owns design alignment, internal planning, boards,
delegation and delivery. Preserve the user's existing approval and scope.

WudiTask only supplies the personal execution identity, rights checks and checkpoints.
Children remain under the main agent's task authority; they do not acquire another
business task. Choose execution details through the repository workflow and current
host capabilities. If no task skill exists, use that repository's instructions and
the user's agreement without inventing a second WudiTask queue or plan protocol.

## Fix WudiTask itself

Follow [tool maintenance](references/maintain.md). Use an isolated worktree and ordinary
push; no Issue or queue entry is required for tool maintenance itself.
