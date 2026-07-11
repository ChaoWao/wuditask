# CLI protocol

## Bootstrap

All operation skills:

1. Read `~/.wuditask/config.json`.
2. Take the absolute `hub_path`.
3. Invoke `python3 <hub_path>/tools/wuditask.py --json <command> ...`.
4. Ask for `$wuditask-install` or `/wuditask-install` when registration is missing or stale.

Keep `--json` before the subcommand. Success is one JSON object with `ok: true`. Failure has `ok: false`, `error.code`, `error.message`, and optional `error.details`.

The CLI obtains the human owner from `gh api user` for distributed writes. Remote mutation is complete only when both `confirmed: true` and `sync.confirmed: true` are present.

## Operation skills

| Operation | Codex | Claude |
| --- | --- | --- |
| Help and routing | `$wuditask` | `/wuditask` |
| Add | `$wuditask-add` | `/wuditask-add` |
| Execute | `$wuditask-execute` | `/wuditask-execute` |
| Dependency check | `$wuditask-dep-check` | `/wuditask-dep-check` |
| Archive | `$wuditask-archive` | `/wuditask-archive` |
| Release | `$wuditask-release` | `/wuditask-release` |
| List or show | `$wuditask-inspect` | `/wuditask-inspect` |
| Update or direct maintenance | `$wuditask-selfupdate` | `/wuditask-selfupdate` |
| Install | `$wuditask-install` | `/wuditask-install` |

## Commands

Mutating commands:

```text
add [--repo owner/name] --title TEXT --goal TEXT
    --accept TEXT [--verify type::value] ...
    [--context TEXT] [--depends TASK_ID] [--priority P0..P3]
    [--link ISSUE_OR_PR_URL]

execute [TASK_ID] [--repo owner/name]

archive TASK_ID --outcome done|failed|cancelled --result TEXT
    [--evidence AC-N=TEXT] ...

release TASK_ID --reason TEXT
```

For a large add request, pass `--spec <file>` or `--spec -`. A spec contains title, repo, goal, context, acceptance criteria, dependencies, priority, and links. Acceptance entries contain a description and verification type/value. The CLI assigns criterion IDs in order.

Read-only commands:

```text
help [workflow|add|execute|dep-check|archive|release|list|show|install|selfupdate]
selfupdate [--check]
dep-check [TASK_ID]
list [--scope open|archive|all] [--repo owner/name]
show TASK_ID
validate
```

## Canonical task narrative

Complete the narrative and execution contract before creating any GitHub resource. When a task has a clear owning GitHub repository, reuse a matching open Issue or PR or create an Issue there, and pass its URL through `--link`. Keep WudiTask goal, context, and acceptance criteria as a concise execution contract. Do not duplicate the complete narrative in both places, create a placeholder PR, or create an Issue in the Task Hub for another repository's work. If Issue creation fails in a suitable repository, report the failure instead of silently falling back to text.

If no suitable GitHub repository exists for the narrative, use the WudiTask text fields as the complete description. Schema v1 still requires a target execution repository.

## Self-update

`selfupdate --check` fetches and reports status without merging. `selfupdate` requires a clean installed clone, validates and tests a temporary candidate clone, and performs only `merge --ff-only`. It never stashes, resets, or rebases the installed clone.

On a non-check result, `reinstall_required: true` means current skill symlinks do not match the local clone. Run the idempotent installer once, without `--replace`; it adds current skill links and removes stale links only when they still target this clone. On `--check`, do not install: `reinstall_required_after_update: true` only predicts that reconciliation will be needed after a successful update. Ordinary content updates through existing skill symlinks do not require reinstall.

One-time migration: the first update from the former two-skill installation still runs old updater bytecode and cannot report new sibling links. Run the existing `$wuditask-install` or `/wuditask-install` once after that update.

`$wuditask-selfupdate fix <request>` and `/wuditask-selfupdate fix <request>` are agent workflows, not CLI syntax. They directly maintain WudiTask in an isolated worktree. They do not create a GitHub Issue or a WudiTask queue item; a PR is created only if direct push is unavailable or review is required.

## Error handling

| Code | Agent behavior |
| --- | --- |
| `insufficient_task_spec` | Ask `details.questions`; retry add |
| `missing_dependency` | Add the dependency first or ask for a corrected ID |
| `no_ready_task` | Report blockers; do not bypass |
| `claim_conflict` | Do not work the task; claim another or ask the user |
| `push_status_unknown` | Fail closed; retry with `details.task_id` and make execute explicit |
| `concurrent_update_exhausted` | Retry without force-pushing |
| `insufficient_archive_evidence` | Run or check missing criteria and add evidence |
| `owner_mismatch` | Stop; the authenticated human does not own the task |
| `invalid_task_data` | Report exact issue paths; a maintainer must repair data |
| `selfupdate_dirty_worktree` | Stop and show local changes; never auto-stash or discard |
| `selfupdate_local_ahead` / `selfupdate_diverged` | Stop and resolve history explicitly |
| `selfupdate_candidate_failed` | Keep the installed version unchanged and report verification |
