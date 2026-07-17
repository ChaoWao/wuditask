---
name: wuditask-selfupdate
description: Update the installed WudiTask clone or directly maintain WudiTask itself. Use for update checks, safe fast-forward upgrades, or requests such as “selfupdate fix” that require changing WudiTask while working in another repository. Direct maintenance uses an isolated worktree and does not create GitHub Issues or WudiTask queue items.
---

# Update or Maintain WudiTask

Distinguish a safe installed-clone update from a direct WudiTask code change.

## Locate the installation

1. Read `~/.wuditask/config.json` and take its absolute `tool_path`.
2. If the config is missing or stale, ask the user to invoke `$wuditask-install` or `/wuditask-install`.
3. Invoke the registered CLI as `python3 <tool_path>/tools/wuditask.py --json ...`.

`selfupdate` operates only on `tool_remote` and `tool_branch`. Task changes in
`hub_remote` never count as tool updates. Never force-push `hub_remote`; it is
shared task data and must advance only through ordinary, fast-forward pushes.

## Update the installed clone

For an update check:

```bash
python3 <tool_path>/tools/wuditask.py --json selfupdate --check
```

For an update:

```bash
python3 <tool_path>/tools/wuditask.py --json selfupdate
```

For `--check`, report status only. Do not run the installer or any other mutation. `reinstall_required_after_update=true` means a later successful update will need link reconciliation.

After a non-check update, report old and new commits plus candidate verification. If the result status is `updated` or `up_to_date` and `reinstall_required=true`, immediately run the idempotent installer once:

```bash
python3 <tool_path>/tools/wuditask.py --json install \
  --hub-remote <hub_remote> \
  --hub-branch <hub_branch>
```

Do not add `--replace`. Stop and report any destination conflict instead of overwriting it. Existing skill content updates through symlinks without this reconciliation. The installer links additions and safely removes stale symlinks only when they still point into this WudiTask clone.

On dirty, local-ahead, or diverged errors, stop and show the exact state. Never stash, reset, rebase, or discard changes in the installed clone automatically.

## Fix WudiTask directly

Treat `$wuditask-selfupdate fix <request>` or `/wuditask-selfupdate fix <request>` as a direct repository-maintenance workflow. `fix` is an agent keyword, not a CLI argument.

1. Record the original repository, branch, commit, and worktree status. Leave its files and active WudiTask state untouched.
2. Safely update the installed WudiTask tool clone as above. Stop if it cannot fast-forward cleanly.
3. Fetch `tool_remote` and create an isolated worktree under `~/.wuditask/worktrees/<slug>` from the configured tool branch, with an agent-owned branch such as `codex/<slug>`. Make no development edits in the installed clone.
4. Implement and test inside that worktree. Run the full WudiTask tool test suite and focused acceptance checks. Do not require live Hub data in the tool candidate.
5. Commit the change and attempt an ordinary push first. If the configured tool branch moved, fetch it, inspect the new commits, rebase only the agent-created branch, rerun the full tests, and retry the ordinary push.
6. Force-push is permitted only for `tool_remote`. Use it for an agent-owned tool branch after a rebase or amend, or for the configured tool branch only when the user explicitly requested a history rewrite and the displaced commits were reviewed. Confirm the target's canonical repository identity exactly matches configured `tool_remote` and differs from `hub_remote`: normalize GitHub SSH and HTTPS URLs to case-insensitive `owner/name`, resolve local repository paths, and stop if equivalence or separation cannot be proved. Raw URL inequality is insufficient. Fetch the target, record its observed OID, rerun the full tests, and use an explicit lease:

   ```bash
   git push \
     --force-with-lease=refs/heads/<branch>:<observed-oid> \
     <tool_remote> HEAD:refs/heads/<branch>
   ```

   Never use bare `--force` or bare `--force-with-lease`. A lease rejection must stop the force-push path: fetch and review the new remote commits instead of refreshing the lease and retrying automatically. Never rewrite another maintainer's branch, a tag, or a release ref.
7. If direct push is unavailable or branch protection requires review, push the agent branch and open a PR. Keep the worktree until the PR is merged or explicitly handed off. Do not treat force-push as a branch-protection bypass.
8. After an ordinary fast-forward reaches the configured tool branch, run installed-clone self-update, reconcile installation if `reinstall_required=true`, and confirm the new commit and skill links. If this maintenance explicitly rewrote the configured tool branch, ordinary self-update must remain fail closed on the resulting divergence. Report the exact local, replaced, and remote OIDs and request separate explicit approval before resetting, replacing, or recloning the installed clone; force-push authorization alone never authorizes those actions.
9. Remove the clean merged worktree and local branch only after the installed state is confirmed. If a configured-branch rewrite leaves installation recovery unresolved, keep the worktree until recovery receives separate approval or the work is explicitly handed off. Then return to the original repository and state.

Do not create a GitHub Issue for this maintenance request. Do not run WudiTask
`add`, `assign`, `execute`, `release`, `unassign`, `archive`, or `delete` for
the maintenance itself. A PR is created only when code review or branch
protection requires it.
