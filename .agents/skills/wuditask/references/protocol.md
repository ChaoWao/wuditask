# CLI protocol

All agent calls use:

```text
python3 <hub>/tools/wuditask.py --json <command> [arguments]
```

Success is one JSON object on stdout with `ok: true`. Failure is one JSON object with `ok: false`, `error.code`, `error.message`, and optional `error.details`.

## Mutating commands

```text
add [--repo owner/name] --title TEXT --goal TEXT
    --accept TEXT [--verify type::value] ...
    [--context TEXT] [--depends TASK_ID] [--priority P0..P3] [--link URL]

execute [TASK_ID] [--repo owner/name]

archive TASK_ID --outcome done|failed|cancelled --result TEXT
    [--evidence AC-N=TEXT] ...

release TASK_ID --reason TEXT
```

For a large add request, pass `--spec <file>` or `--spec -`. A spec contains title, repo, goal, context, acceptance_criteria, dependencies, priority, and links. Acceptance entries contain description and verification with type/value. The CLI generates IDs as `AC-1`, `AC-2`, and so on.

## Read commands

```text
help [workflow|add|execute|dep-check|archive|release|list|show|install]
dep-check [TASK_ID]
list [--scope open|archive|all] [--repo owner/name]
show TASK_ID
validate
```

`help` is identity-free and read-only. Agent invocations are `$wuditask help [topic]` in Codex and `/wuditask help [topic]` in Claude.

## Error handling

| Code | Agent behavior |
| --- | --- |
| `insufficient_task_spec` | Ask `details.questions`; retry add |
| `missing_dependency` | Add dependency first or ask for a corrected ID |
| `no_ready_task` | Report blockers; do not bypass |
| `claim_conflict` | Do not work the task; claim another or ask user |
| `push_status_unknown` | Do not act; retry with `details.task_id` (for execute, always make it explicit) |
| `concurrent_update_exhausted` | Wait briefly and retry; never force-push |
| `insufficient_archive_evidence` | Run/check missing criteria and add evidence |
| `owner_mismatch` | Stop; the authenticated human does not own the task |
| `invalid_task_data` | Report exact issue paths; maintainer must repair data |

Remote mutation completion requires both `confirmed: true` and `sync.confirmed: true`.
