---
name: wuditask-install
description: Register a cloned WudiTask repository for Codex and Claude agents on the current machine. Use when a user asks to install, set up, register, relocate, repair, or update WudiTask access, or when the operational wuditask skill reports a missing or stale ~/.wuditask/config.json path.
---

# Install WudiTask

Register this clone by running its own Python installer. Do not pip/npm install anything and do not copy skill files manually.

## Resolve the clone

1. Prefer the current Git repository root when it contains both `tools/wuditask.py` and `.agents/skills/wuditask`.
2. Otherwise resolve this SKILL.md's real path and walk upward to the directory containing `tools/wuditask.py`.
3. For repair after a move, use the new clone path supplied by the user.
4. Refuse a directory that lacks the tool or both WudiTask skills.

## Register

Run:

```bash
python3 HUB/tools/wuditask.py --hub HUB --json install
```

Confirm the JSON reports:

- `~/.wuditask/config.json` with the absolute hub path;
- `wuditask` and `wuditask-install` links under both `~/.agents/skills` and `~/.claude/skills`;
- `~/.local/bin/wuditask` linked to the repository's Python entry point.

These are symbolic links, not copied files. A normal `git pull` in the same clone updates both products immediately; do not reinstall after updates. If a long-running agent session has cached old instructions, reopen the session instead. Reinstall only when the clone moves, is replaced at another path, or a link is damaged.

If `launcher_on_path` is false, mention the launcher path; agents can still call the absolute Python entry point from config.

If installation returns `install_path_exists`, inspect and tell the user which destination conflicts. Do not use `--replace` until the user explicitly approves. When approved, rerun with `--replace`; the installer renames existing content to a timestamped backup.

After a successful install, run:

```bash
python3 HUB/tools/wuditask.py --json validate
```

Report the registered absolute path and validation result. Rerun this skill whenever the clone moves.
