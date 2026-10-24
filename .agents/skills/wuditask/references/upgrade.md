# Upgrade the existing installation

User intent to upgrade authorizes converting the existing execution storage and reconciling
managed links. Keep the original tool repository and execution repository. Never create an
alternate branch to evade old claims, and never require proof that old processes stopped.

## Already installed

Read `~/.wuditask/config.json` for `tool_path`, `tool_remote`, `tool_branch`, `hub_remote`,
and `hub_branch`. Use the registered tool clone; leave dirty or diverged local work intact.

- Current CLI: `python3 TOOL/tools/wuditask.py --json upgrade`.
- Old CLI lacking `upgrade`: run its `--json selfupdate`, then invoke the now updated
  `python3 TOOL/tools/wuditask.py --json coord upgrade`. Re-read this skill after update.
- If even selfupdate is unavailable, inspect the registered clone for local changes,
  fetch its configured origin/branch, and fast-forward only when clean. Then run
  `coord upgrade --remote ORIGINAL_EXECUTION_REMOTE --branch ORIGINAL_BRANCH`.
- If config is missing, identify the two existing clones from the user's context. Read
  their origins/branches. Do not guess another repository; ask only if these cannot be
  identified. Run `coord upgrade --remote ORIGINAL_EXECUTION_REMOTE --branch ORIGINAL_BRANCH`
  from the updated tool clone.

The upgrade converts the original execution branch in one normal Git transaction. Old
queue files and manifest are retained under `data/legacy/`; old active entries are historical,
not completed or automatically resumed. Current personal execution claims already in the
new format remain intact. Legacy clients reject the updated manifest on their next operation.
No stop receipt is required; uncontrolled old processes may still finish pending writes.

The temporary `personal-execution` branch is no longer the default. An installation bound
there is upgraded to the original main only when that branch has no execution claims.
If it contains history, stop and report the required transfer; do not discard it. Deleting
that remote branch is a separate cleanup after checking no data was lost; upgrade does not
silently delete arbitrary branches.

After remote conversion, register the single `$wuditask` skill and CLI, remove stale symlinks
owned by the registered WudiTask installation, and bind personal configuration to that same
remote/branch. Do not overwrite unrelated files with `--replace`. A partial failure is
reported and retryable; do not claim completion until config, links and storage validate.

If an old execution clone is known locally, fast-forward it to its origin branch only if
clean. A dirty/diverged old clone remains untouched and should not be used with the old CLI.
The coordinator accesses the remote through its cache, so a stale local clone is not a
second authority. Repeat the upgrade on each machine; no agent processes are automatically
restarted or enlisted.

Verify `coord inventory`, the installed single skill, and matching execution branch in
both local configuration files. Report the tool commit, storage branch, preserved history,
and any local clone that still needs attention. New long-running sessions may be needed
for hosts that cache skill catalogs.

## New installation

Clone the tool and use the existing execution repository's original branch (normally main):

```bash
python3 tools/wuditask.py --json coord setup --remote EXECUTION_REMOTE --branch main
```

The remote branch must already exist; setup upgrades it in place. Shared public storage
must not contain credentials, private conversation or sensitive recovery material.
Use SSH/Git credential storage, never credentials embedded in remote URLs.
