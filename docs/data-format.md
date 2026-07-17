# WudiTask 数据格式 v3

任务与删除回执的机器契约分别位于 `schemas/task.schema.json` 和
`schemas/deletion-receipt.schema.json`。Hub 必须精确声明：

```json
{
  "schema_version": 3,
  "tool_api_version": 4
}
```

v3 是无兼容切换：工具不读取 v1/v2 task，不接受旧 `claim`、text source 或
重复 narrative 字段。升级必须在同一个 Hub commit 中迁移 manifest、全部 open
和 archive task，并固定兼容的新工具 commit。

## 两个事实源

GitHub Issue/PR 是业务合同：

- title、body、goal、context 与 acceptance；
- assignees、PR author、reviews、checks 与交付终态；
- discussion 和实现链接。

Task Hub 是最小协调记录：

- 执行仓、priority 与跨仓 dependencies；
- 当前 `active_agents`；
- 归档 outcome、证据和参与 runs。

Hub 不复制 GitHub narrative，也不保存 owner。owner 每次从 canonical source
实时派生：

- PR source：PR author + PR assignees；
- Issue source：Issue assignees + closing-linked PR authors；普通 timeline mention
  不产生 owner。

owner 与 active agent 正交。assignment 不代表已经执行。execute 自动选择
unowned task，或显式选择当前 login 尚非 owner 的 task 时，会先执行并确认独立
的 self-assignment，然后才启动 Hub run。

## 存储与状态

- open task：`data/open/<id>.json`
- archived task：`data/archive/<year>/<id>.json`
- deletion receipt：`data/deletions/<receipt-id>.json`

状态由位置、dependencies、`active_agents` 与 `completion` 派生：

| 条件 | coordination state |
| --- | --- |
| open、依赖完成、`active_agents=[]` | `ready` |
| open、依赖未完成、`active_agents=[]` | `blocked` |
| open、`active_agents` 非空 | `in_progress` |
| archive | `completion.outcome` |

`in_progress` 表示至少一个 agent run 已由 Hub push 确认，不表示独占。多个不同
login 可同时出现。GitHub delivery 仍独立派生为 `unstarted`、`assigned`、
`implementing`、`review`、`ready_to_merge`、`verification_needed`、
`cancelled` 或 `unavailable`。

## Open task

v3 open task 只允许以下字段：

| 字段 | 说明 |
| --- | --- |
| `schema_version` | 固定为 `3` |
| `id` | `WDT-YYYYMMDDTHHMMSSZ-XXXXXX` |
| `repo` | 实际执行仓，GitHub `owner/name` |
| `source` | 唯一 canonical GitHub Issue/PR |
| `created_by` | 创建任务的可读 GitHub login |
| `priority` | `P0` 到 `P3` |
| `created_at` | UTC RFC 3339 时间 |
| `dependencies` | 已存在的 WudiTask IDs |
| `active_agents` | 当前执行 runs 的数组 |

完整示例：

```json
{
  "schema_version": 3,
  "id": "WDT-20260711T120000Z-A1B2C3",
  "repo": "acme/api",
  "source": {
    "kind": "github_issue",
    "repo": "acme/api",
    "number": 42
  },
  "created_by": "alice",
  "priority": "P1",
  "created_at": "2026-07-11T12:00:00Z",
  "dependencies": [],
  "active_agents": [
    {
      "login": "alice",
      "run_id": "WDX-0123456789ABCDEF01234567"
    },
    {
      "login": "bob",
      "run_id": "WDX-89ABCDEF0123456789ABCDEF"
    }
  ]
}
```

每个 login 最多一项，比较不区分大小写。`run_id` 标识该 login 的一次具体
执行，用于阻止旧 release/archive 删除或确认同一 login 的新 run；它不是密码。
Pages 只展示 login，不发布 run_id。

## Canonical source

执行仓 Issue：

```json
{"kind": "github_issue", "repo": "acme/api", "number": 42}
```

Pull request：

```json
{"kind": "github_pull_request", "repo": "acme/api", "number": 88}
```

当执行仓不能承载 Issue 时，Hub Issue 仍是 canonical GitHub Issue，但保留显式
fallback kind 与原因：

```json
{
  "kind": "github_issue_fallback",
  "repo": "acme/wuditask-hub",
  "number": 17,
  "fallback_reason": "The execution repository has Issues disabled."
}
```

URL 由 kind/repo/number 推导。不存在 text source；临时网络或权限失败必须报错，
不能降级生成本地描述。Issue/PR body 必须在 add 前包含完整 scope 和 acceptance。

## Assignment 与 active agents

`assign TASK_ID [--to LOGIN]` 和 `unassign TASK_ID [--from LOGIN]` 只修改
canonical GitHub source 的 assignee。默认 login 是当前认证用户；对另一个 login
操作必须有明确用户授权。PR authorship 不能被 unassign。

`release` 只修改 Hub；`execute` 的 Hub start 前可能有独立 GitHub self-assignment：

- 无 ID 的 execute 先选择 assigned-to-current-login 且 idle 的 ready task，再
  选择 unowned ready task；不会选择只由其他人拥有的工作；
- 自动选择 unowned task，或显式选择当前 login 尚非 owner 的 task 时，先
  self-assign 当前 login 并确认 owner；
- execute 添加 `{login,run_id}`，普通 Hub push 是启动确认点；
- self-assignment 与 Hub start 不是原子事务；后者失败不回滚前者；
- release 只在 login 与 run_id 同时匹配时删除该项；
- unassign 在目标 login 仍 active 时拒绝，要求先逐 run release；
- 一个 agent 的变更不得覆盖其他 login 的 entry。

## Acceptance 与 archive

acceptance 只存在于 canonical Issue/PR，不存在 Hub `acceptance_criteria`。归档
`done` 时，调用方读取 source、完成其验收、确认 Issue completed 或 PR merged，
并提交至少一条具体 evidence。

Archive 原子清空 `active_agents`，并新增：

```json
{
  "completion": {
    "outcome": "done",
    "completed_at": "2026-07-17T12:00:00Z",
    "completed_by": "alice",
    "result": "Validation implemented and verified.",
    "evidence": [
      "python3 -m unittest tests.test_upload: 12 passed",
      "Merged pull request: https://github.com/acme/api/pull/88"
    ],
    "participants": [
      {"login": "alice", "run_id": "WDX-0123456789ABCDEF01234567"},
      {"login": "bob", "run_id": "WDX-89ABCDEF0123456789ABCDEF"}
    ]
  }
}
```

`done` 的 `completed_by` 必须是 participants 中自己的 matching `run_id`。对
`failed`/`cancelled`，只要归档前存在 active agents，也使用同一规则，并把当时
全部 active entries 保存到 participants 后清空全集。若归档前没有 active agent，
participants 可以为空，但 `completed_by` 必须等于 task `created_by`，且 CLI
必须省略 `--run-id`；旧 run ID 会被拒绝而不是忽略。这个 creator-only 终态路径
覆盖未认领或已经 release 的工作。

`done` 解除下游依赖；`failed` 与 `cancelled` 永不解除。Issue
`CLOSED/NOT_PLANNED` 只能对应 cancelled；nonterminal 或 unavailable delivery
不能伪装成 terminal。

## 删除误建 archive

`delete` 仅处理用户明确指定的错误 archive。单个 Hub commit 删除完整目标批次，
并写入持久回执：

```json
{
  "receipt_version": 2,
  "id": "WDR-DE897E092948A083BE2A1BC1",
  "task_ids": ["WDT-20260711T120000Z-A1B2C3"],
  "reason": "This record was created by mistake",
  "deleted_by": "alice",
  "deleted_at": "2026-07-17T12:00:00Z"
}
```

回执 ID 由排序 task IDs、规范化 reason 和 authenticated login 确定。回执永久
保留被删 ID，阻止重建造成 ABA。删除不修改 GitHub source，也不能清除 Git
历史、旧 clone 或 Pages artifact。

## Pages snapshot v3

Pages snapshot schema v3 合并 Hub coordination 与 live GitHub delivery。它可包含
source title/body、owners、reviews/checks、completion evidence 与 active-agent
logins；不得包含 active `run_id`。GitHub 查询失败必须保留 unavailable，不能把
unknown owners 渲染成无人负责。
