---
name: wuditask-selfupdate
description: Update the installed WudiTask clone or directly maintain WudiTask itself. Use for update checks, safe fast-forward upgrades, or requests such as “selfupdate fix” that require changing WudiTask while working in another repository. Direct maintenance uses an isolated worktree and does not create GitHub Issues or WudiTask queue items.
---

# Update or Maintain WudiTask

Distinguish a safe installed-clone update from a direct WudiTask code change.

## Locate the installation

1. Read `~/.wuditask/config.json` and take its absolute `hub_path`.
2. If the config is missing or stale, ask the user to invoke `$wuditask-install` or `/wuditask-install`.
3. Invoke the registered CLI as `python3 <hub_path>/tools/wuditask.py --json ...`.

## Update the installed clone

For an update check:

```bash
python3 <hub_path>/tools/wuditask.py --json selfupdate --check
```

For an update:

```bash
python3 <hub_path>/tools/wuditask.py --json selfupdate
```

For `--check`, report status only. Do not run the installer or any other mutation. `reinstall_required_after_update=true` means a later successful update will need link reconciliation.

After a non-check update, report old and new commits plus candidate verification. If the result status is `updated` or `up_to_date` and `reinstall_required=true`, immediately run the idempotent installer once:

```bash
python3 <hub_path>/tools/wuditask.py --hub <hub_path> --json install
```

Do not add `--replace`. Stop and report any destination conflict instead of overwriting it. Existing skill content updates through symlinks without this reconciliation. The installer links additions and safely removes stale symlinks only when they still point into this WudiTask clone.

On dirty, local-ahead, or diverged errors, stop and show the exact state. Never stash, reset, rebase, or discard changes in the installed clone automatically.

## Fix WudiTask directly

Treat `$wuditask-selfupdate fix <request>` or `/wuditask-selfupdate fix <request>` as a direct repository-maintenance workflow. `fix` is an agent keyword, not a CLI argument.

1. Record the original repository, branch, commit, and worktree status. Leave its files and active WudiTask state untouched.
2. Safely update the installed WudiTask clone as above. Stop if it cannot fast-forward cleanly.
3. Fetch the WudiTask remote and create an isolated worktree under `~/.wuditask/worktrees/<slug>` from current `origin/main`, with an agent-owned branch such as `codex/<slug>`. Make no development edits in the installed clone.
4. Implement and test inside that worktree. Run the full WudiTask test suite, task-data validation, and focused acceptance checks.
5. Commit the change. Attempt an ordinary push to `main`. If `main` moved, fetch, rebase only the agent-created branch onto `origin/main`, rerun tests, and retry. Never force-push.
6. If direct push is unavailable or branch protection requires review, push the agent branch and open a PR. Keep the worktree until the PR is merged or explicitly handed off.
7. After the change reaches `main`, run installed-clone self-update, reconcile installation if `reinstall_required=true`, confirm the new commit and skill links, remove the clean merged worktree and local branch, and return to the original repository and state.

Do not create a GitHub Issue for this maintenance request. Do not run WudiTask `add`, `execute`, `archive`, or `release` for the maintenance itself. A PR is created only when code review or branch protection requires it.

Read [../wuditask/references/protocol.md](../wuditask/references/protocol.md) for self-update errors.
