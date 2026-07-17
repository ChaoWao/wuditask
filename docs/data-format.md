# WudiTask 数据格式 v2

任务与删除回执的机器契约分别位于 `schemas/task.schema.json` 和
`schemas/deletion-receipt.schema.json`；Hub 必须声明当前任务/API 版本：

```json
{
  "schema_version": 2,
  "tool_api_version": 3
}
```

WudiTask 不支持混用 v1/v2。升级时必须一次性迁移 Hub manifest 与所有任务。

## 存储与状态

- open task：`data/open/<id>.json`
- archived task：`data/archive/<year>/<id>.json`
- deletion receipt：`data/deletions/<receipt-id>.json`

正常任务不会删除。用户明确确认误建的 archived record 可以通过受保护的
`delete` 命令从当前快照移除，并在当前树中写入持久回执。任务
状态仍由位置、依赖、`claim` 和 `completion` 推导，不保存可漂移的
WudiTask `status`：

| 条件 | coordination state |
| --- | --- |
| open、`claim=null`、依赖完成 | `ready` |
| open、`claim=null`、依赖未完成 | `blocked` |
| open、`claim` 非空 | `in_progress` |
| archive | `completion.outcome` |

GitHub delivery 是另一条正交状态，通过 canonical `source` 实时派生：
`unstarted`、`assigned`、`implementing`、`review`、`ready_to_merge`、
`verification_needed`、`cancelled`、`text_only` 或 `unavailable`。它不写回任务
JSON，也不直接解除 WudiTask 依赖。

## 基础字段

| 字段 | 说明 |
| --- | --- |
| `schema_version` | 固定为 `2` |
| `id` | `WDT-YYYYMMDDTHHMMSSZ-XXXXXX` |
| `title` | 简短可扫描标题 |
| `repo` | 实际执行仓，固定为 GitHub `owner/name` |
| `source` | 唯一 canonical Issue、PR 或解释过的 text source |
| `created_by` | 添加任务的 GitHub human identity |
| `priority` | `P0` 到 `P3` |
| `created_at` | UTC RFC 3339 时间 |
| `goal` | 期望结果 |
| `context` | 执行约束、背景、入口与非目标 |
| `acceptance_criteria` | 至少一条可验证完成条件 |
| `dependencies` | 已存在的 WudiTask ID |
| `claim` | 独占执行租约或 `null` |
| `links` | 辅助资料；不再承载 canonical source |
| `completion` | 仅 archived task 存在 |

schema v2 没有 `owner`。业务责任人来自 GitHub assignee；claim holder 直接由
`claim.github_login/github_id` 表示。

## Canonical source

### 执行仓 Issue

```json
{
  "kind": "github_issue",
  "repo": "acme/api",
  "number": 42
}
```

### Pull request

```json
{
  "kind": "github_pull_request",
  "repo": "acme/api",
  "number": 88
}
```

URL 由 `kind + repo + number` 唯一推导，避免重复字段漂移。

### Hub fallback Issue

当业务仓关闭 Issues、当前用户没有建 Issue 权限，或跨仓工作没有合适的单一
归属仓时，canonical Issue 可以在配置的 Task Hub：

```json
{
  "kind": "github_issue_fallback",
  "repo": "acme/wuditask-hub",
  "number": 17,
  "fallback_reason": "The execution repository has Issues disabled."
}
```

`github_issue` 与 `github_pull_request` 必须位于执行仓，且没有
`fallback_reason`。只有 `github_issue_fallback` 可以跨仓；它必须指向安装配置的
Hub，并包含 `fallback_reason`。CLI 在写 Hub 前还会确认该 Issue/PR 存在且可读。
这让 JSON Schema 与 runtime 使用同一种结构，而不是靠可选字段猜测语义。Hub
Issue 的正文必须说明执行仓、fallback 原因、范围与验收意图。

### Text source

```json
{
  "kind": "text",
  "reason": "Neither the execution repository nor the Hub can host an Issue."
}
```

Text 只是最后手段。暂时的网络、认证或 API 故障不能静默降级成 text source。

## Identity 与 claim

Identity：

```json
{
  "login": "octocat",
  "github_id": 583231
}
```

Claim：

```json
{
  "token": "nonce",
  "github_login": "octocat",
  "github_id": 583231,
  "claimed_at": "2026-07-11T12:04:10Z"
}
```

`token` 标识一次具体租约，用于安全补偿，不是秘密。远端写操作始终通过
`gh api user` 验证 human identity；不可变的 `github_id` 是授权键，login 用于
展示并在用户改名后的下一次 execute 中刷新。agent 不是 owner。

## 验收与归档

Criterion：

```json
{
  "id": "AC-1",
  "description": "Malformed files return HTTP 400",
  "verification": {
    "type": "command",
    "value": "python3 -m unittest tests.test_upload"
  }
}
```

`verification.type` 只能是 `command`、`file`、`manual` 或 `url`。

`completion.outcome` 是 `done`、`failed` 或 `cancelled`。`done` 必须同时满足：

1. canonical Issue 当前 completed（通常由 closing PR merge 触发），或 canonical
   PR merged；open/reopened Issue 仍是 active；
2. 每条 criterion 都有 `passed` 与非空 evidence；
3. 当前 claim holder 完成远端 archive push。

`failed`/`cancelled` 不要求先领取：只要任务当前无人领取，就可以用一次普通 Hub
push 原子归档，即使依赖仍 blocked。若已有 claim，仍只允许对应 holder 归档。
GitHub-backed `cancelled` 还必须先将 canonical delivery 以 `NOT_PLANNED` 关闭。
`failed` 接受 `NOT_PLANNED`，也接受 delivery 已完成但 WudiTask 验收失败；活跃
delivery 和未知状态都不能归档。text source 没有这层外部 guard。这些 outcome
永不解除下游依赖。

Issue `CLOSED/NOT_PLANNED` 只能映射为 cancelled 候选，不能作为 done。只有
WudiTask archive `done` 且证据完整才解除下游依赖。

## 删除误建 archive

delete 不改变 task schema v2，但 Hub 必须使用 tool API v3 识别删除回执。
一个批次只有在所有目标都存在于 archive、ID 唯一且没有批次外
open/archive task 依赖目标时，才会由单个 Hub commit 移除所有
目标并写入一份回执。批次内部的依赖可随完整批次一起删除。

回执是非 task JSON，精确字段为：

```json
{
  "receipt_version": 1,
  "id": "WDR-DE897E092948A083BE2A1BC1",
  "task_ids": [
    "WDT-20260711T120000Z-A1B2C3",
    "WDT-20260711T120001Z-D4E5F6"
  ],
  "reason": "Both records were created by mistake",
  "deleted_by": {
    "login": "alice",
    "github_id": 1001
  },
  "deleted_at": "2026-07-17T12:00:00Z"
}
```

`task_ids` 必须非空、唯一且排序；reason 非空；`deleted_by` 是已验证的
GitHub login 与不可变 numeric ID；`deleted_at` 是 UTC。确定性 receipt ID
由排序后的 `task_ids`、去除首尾空白的 reason 和 `deleted_by.github_id`
派生，不包含时间或可变 login。

当前数据契约禁止任务与任何回执重复 ID，也禁止两份回执覆盖同一
任务 ID。因此已删除 ID 永久保留，后续 `add --id` 不能重建，避免
ABA。幂等重试和远端 reconciliation 同时要求确定性回执匹配、所有
目标任务都不存在；只有目标缺席不足以确认。不同 actor 或 reason
会得到不同回执，不是同一操作。

delete 不修改 `source` 指向的 GitHub Issue/PR。Git 历史、旧 clone 和已发布
Pages artifact 仍可能包含原任务，因此 delete 不是 privacy/secret erasure。

## 完整 open task 示例

```json
{
  "schema_version": 2,
  "id": "WDT-20260711T120000Z-A1B2C3",
  "title": "Harden upload validation",
  "repo": "acme/api",
  "source": {
    "kind": "github_issue",
    "repo": "acme/api",
    "number": 42
  },
  "created_by": {
    "login": "alice",
    "github_id": 1001
  },
  "priority": "P1",
  "created_at": "2026-07-11T12:00:00Z",
  "goal": "Reject malformed uploads before object storage.",
  "context": ["Preserve the public API."],
  "acceptance_criteria": [
    {
      "id": "AC-1",
      "description": "Malformed files return HTTP 400.",
      "verification": {
        "type": "command",
        "value": "python3 -m unittest tests.test_upload"
      }
    }
  ],
  "dependencies": [],
  "claim": null,
  "links": []
}
```
