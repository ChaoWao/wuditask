---
name: wuditask
description: Explain WudiTask and route legacy monolithic invocations to operation-specific skills. Use when a user asks how WudiTask works, invokes help, explicitly uses $wuditask or /wuditask instead of a dedicated skill, or needs help choosing an operation. Prefer the dedicated operation skill for new requests.
---

# WudiTask

This file defines only help and routing. Operation-specific mutation rules live in sibling skills.

## Locate the CLI

1. Read `~/.wuditask/config.json`.
2. Take the absolute `hub_path`.
3. Invoke `python3 <hub_path>/tools/wuditask.py --json ...`.
4. If the config is missing or the path no longer exists, ask the user to invoke `$wuditask-install` (Codex) or `/wuditask-install` (Claude).

Keep `--json` before the subcommand.

## Help

For `$wuditask help [topic]`, `/wuditask help [topic]`, or a question about how WudiTask works:

1. Run `python3 <hub_path>/tools/wuditask.py --json help [topic]`.
2. Explain the returned workflow and commands in the user's language.
3. Show the matching dedicated skill from the routing table below.
4. Do not execute a mutating task command.

Supported help topics are `workflow`, `add`, `execute`, `dep-check`, `archive`, `release`, `list`, `show`, `install`, and `selfupdate`.

## Legacy operation invocation

If the user explicitly invokes `$wuditask <operation>` or `/wuditask <operation>` for an operation other than help:

1. Select the sibling skill from the table below.
2. Read that sibling `SKILL.md` completely from `<hub_path>/.agents/skills/<skill-name>/SKILL.md`. This source path works even during migration before the new skill symlink has been installed.
3. Follow it in the same turn, including its safety gates.
4. If the dedicated symlink is not installed yet, complete the current request from the source instructions and tell the user to run `$wuditask-install` or `/wuditask-install` once. Otherwise mention the preferred dedicated invocation for future use.

Do not reproduce or improvise the mutation workflow from this router alone.

## Route operations

| User intent | Codex skill | Claude skill |
| --- | --- | --- |
| Add or record work | `$wuditask-add` | `/wuditask-add` |
| Claim or start work | `$wuditask-execute` | `/wuditask-execute` |
| Check blockers or readiness | `$wuditask-dep-check` | `/wuditask-dep-check` |
| Archive done, failed, or cancelled work | `$wuditask-archive` | `/wuditask-archive` |
| Return owned work to the queue | `$wuditask-release` | `/wuditask-release` |
| List or show shared state | `$wuditask-inspect` | `/wuditask-inspect` |
| Update or directly fix WudiTask | `$wuditask-selfupdate` | `/wuditask-selfupdate` |
| Register or repair local access | `$wuditask-install` | `/wuditask-install` |

Read [references/protocol.md](references/protocol.md) only when explaining the CLI contract or an unfamiliar error.
