---
name: wuditask-install
description: Register the complete WudiTask skill suite and CLI symlinks for Codex and Claude from a cloned WudiTask repository. Use when a user asks to install, set up, register, relocate, repair, or reconcile WudiTask access, or when any WudiTask skill reports missing or stale configuration or links.
---

# Install WudiTask

Register this clone by running its own Python installer. Do not pip/npm install anything and do not copy skill files manually.

## Resolve the clone

1. Prefer the current Git repository root when it contains `tools/wuditask.py` and `.agents/skills/`.
2. Otherwise resolve this SKILL.md's real path and walk upward to the directory containing `tools/wuditask.py`.
3. For repair after a move, use the new clone path supplied by the user.
4. Refuse a directory that lacks the tool, lacks a required WudiTask skill, or contains an unexpected skill.

## Register

Run:

```bash
python3 HUB/tools/wuditask.py --hub HUB --json install
```

Confirm the JSON reports:

- `~/.wuditask/config.json` with the absolute hub path;
- the complete reported skill suite linked under both `~/.agents/skills` and `~/.claude/skills`;
- `~/.local/bin/wuditask` linked to the repository's Python entry point.

These are symbolic links, not copied files. Prefer `/wuditask-selfupdate` or `$wuditask-selfupdate` for verified future updates. Existing skill content updates immediately through the symlinks. After a non-check self-update reports `reinstall_required=true`, run this installer once without `--replace` to reconcile the suite. It removes a stale skill link only when that symlink still targets this clone; it never deletes unrelated skills or regular files. Also reinstall when the clone moves, is replaced at another path, or a link is damaged. If a long-running agent session has cached old instructions, reopen the session afterward.

If `launcher_on_path` is false, mention the launcher path; agents can still call the absolute Python entry point from config.

If installation returns `install_path_exists`, inspect and tell the user which destination conflicts. Do not use `--replace` until the user explicitly approves. When approved, rerun with `--replace`; the installer renames existing content to a timestamped backup.

After a successful install, run:

```bash
python3 HUB/tools/wuditask.py --json validate
```

Report the registered absolute path and validation result. Rerun this skill whenever the clone moves.
