---
name: wuditask
description: Use WudiTask for GitHub work overview, personal cross-machine task coordination, installation, upgrade, or tool maintenance. One entry for upgrading an old two-repository installation, starting a task, taking over here, saving progress and finishing. GitHub remains the business record.
---

# WudiTask

One user entry: `$wuditask` (or `/wuditask`). Route by the user's intent; handle CLI details,
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

## Fix WudiTask itself

Follow [tool maintenance](references/maintain.md). Use an isolated worktree and ordinary
push; no Issue or queue entry is required for tool maintenance itself.
