---
name: wuditask-install
description: Register the complete WudiTask skill suite and CLI symlinks from a cloned tool repository with a separate task Hub remote. Use to install, set up, relocate, repair, or reconcile WudiTask access and stale skill links.
---

# Install WudiTask

Register the tool clone through its Python installer. Do not pip/npm install it
and do not copy skill files manually.

## Personal coordination (preferred for new setups)

Use the unified personal coordination skill `$wuditask-coordinate` for task work.
For a new setup, run `coord setup --remote REMOTE --branch personal-execution` from
this clone. It uses a separate execution branch without migrating legacy tasks and
registers the same skill suite and CLI. The detailed registration flow below also
supports an already initialized personal execution branch.

## Resolve the tool and Hub

1. Use a Git repository root containing `tools/wuditask.py` and
   `.agents/skills/`.
2. Obtain the separate task Hub remote and branch; never use the tool origin as
   the Hub.
3. Refuse a clone missing a required skill or containing an unexpected one.

## Register

```bash
python3 TOOL/tools/wuditask.py --json install \
  --hub-remote https://github.com/OWNER/wuditask-hub.git \
  --hub-branch main
```

Confirm the result reports:

- `~/.wuditask/config.json` schema v2 with `tool_path`, tool remote/branch, and
  the separate Hub remote/branch;
- a persistent bare `hub_cache` under `$XDG_CACHE_HOME/wuditask` or
  `~/.cache/wuditask`;
- thirteen skills linked under both `~/.agents/skills` and
  `~/.claude/skills`: add, archive, assign, check, delete, execute, install,
  list, release, selfupdate, show, unassign, and coordinate;
- `~/.local/bin/wuditask` linked to the repository entry point.

Links are symbolic. After selfupdate reports `reinstall_required=true`, rerun
install once without `--replace`; this installs new links and removes only
stale WudiTask links still targeting the registered clone. It must remove the
retired dep-check/reconcile links without touching unrelated skills or files.

If installation returns `install_path_exists`, inspect the conflict. Use
`--replace` only with explicit user approval; existing content is preserved as
a timestamped backup. Mention the launcher path when it is not on `PATH`.

The installer validates an isolated operation worktree for the Hub before
changing local registration. After success, run:

```bash
python3 TOOL/tools/wuditask.py --json validate  # legacy storage only
# For personal storage use: coord inventory
```

Report tool path, Hub remote/branch, bare cache, skill inventory, and
validation result. Reopen long-running agent sessions that cached retired
skills.
