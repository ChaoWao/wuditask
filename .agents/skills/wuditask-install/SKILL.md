---
name: wuditask-install
description: Register the complete WudiTask skill suite and CLI symlinks from a cloned tool repository, together with a separate task Hub remote. Use when a user asks to install, set up, register, relocate, repair, or reconcile WudiTask access, or when any WudiTask skill reports missing or stale configuration or links.
---

# Install WudiTask

Register the tool clone by running its own Python installer. Do not pip/npm install anything and do not copy skill files manually.

## Resolve the tool and Hub

1. Prefer the current Git repository root when it contains `tools/wuditask.py` and `.agents/skills/`.
2. Otherwise resolve this SKILL.md's real path and walk upward to the directory containing `tools/wuditask.py`.
3. For repair after a move, use the new clone path supplied by the user.
4. Refuse a directory that lacks the tool, lacks a required WudiTask skill, or contains an unexpected skill.
5. Obtain the task Hub Git remote and branch. The Hub must be a separate repository containing a compatible `hub.json`; do not use the tool repository remote as the Hub.

## Register

Run:

```bash
python3 TOOL/tools/wuditask.py --json install \
  --hub-remote https://github.com/OWNER/wuditask-hub.git \
  --hub-branch main
```

Confirm the JSON reports:

- `~/.wuditask/config.json` schema v2 with `tool_path`, `tool_remote`, `tool_branch`, `hub_remote`, and `hub_branch`;
- `hub_cache` under `$XDG_CACHE_HOME/wuditask` or `~/.cache/wuditask`, pointing to the persistent bare cache selected by the Hub remote and branch;
- the complete reported skill suite linked under both `~/.agents/skills` and `~/.claude/skills`;
- `~/.local/bin/wuditask` linked to the repository's Python entry point.

These are symbolic links, not copied files. Prefer `/wuditask-selfupdate` or `$wuditask-selfupdate` for verified future updates. Existing skill content updates immediately through the symlinks. After a non-check self-update reports `reinstall_required=true`, run this installer once without `--replace` to reconcile the suite. It removes a stale skill link only when that symlink still targets this clone; it never deletes unrelated skills or regular files. Also reinstall when the clone moves, is replaced at another path, or a link is damaged. If a long-running agent session has cached old instructions, reopen the session afterward.

If `launcher_on_path` is false, mention the launcher path; agents can still call the absolute Python entry point from config.

If installation returns `install_path_exists`, inspect and tell the user which destination conflicts. Do not use `--replace` until the user explicitly approves. When approved, rerun with `--replace`; the installer renames existing content to a timestamped backup.

The installer initializes or refreshes the persistent bare Hub cache and
validates an isolated operation worktree before changing links or config. It
registers the complete ten-skill suite, including the read-only GitHub/WudiTask
reconciliation workflow. A validation failure may leave reusable Git objects
in this disposable cache, but it must not create config, skill links, or the
launcher. After a successful install, run a remote validation once more through
the registered CLI:

```bash
python3 TOOL/tools/wuditask.py --json validate
```

Report the registered tool path, Hub remote and branch, bare cache path, and validation result. Rerun this skill whenever the tool clone moves or the Hub remote changes.
