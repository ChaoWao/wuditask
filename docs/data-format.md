# WudiTask 数据格式 v1

本文档是人类可读的规范；机器可读版本位于 `schemas/task.schema.json`，最终校验行为以仓库内同版本的 `wuditask validate` 为准。

## 存储规则

每个任务独占一个 JSON 文件：

- 未归档：`data/open/<id>.json`
- 已归档：`data/archive/<完成年份>/<id>.json`

文件名必须等于任务 `id`。任务不会删除；`archive` 将同一个 JSON 从 open 移到 archive 并加入 `completion`。这样，不同 agent 修改不同任务时通常只接触不同文件，Git 冲突概率很低。

JSON 采用 UTF-8、两空格缩进、末尾换行。字段名固定，未知字段会被拒绝。

## 状态不是字段

WudiTask 不保存可漂移的 `status`：

| 条件 | 推导状态 |
| --- | --- |
| 位于 open，`claim=null`，所有依赖已完成 | `ready` |
| 位于 open，`claim=null`，至少一个依赖未完成 | `blocked` |
| 位于 open，`claim` 非空 | `in_progress` |
| 位于 archive | `completion.outcome`：`done`、`failed` 或 `cancelled` |

只有归档结果为 `done`，并且每条验收标准都有 `passed` 与非空证据，才会解除下游任务的依赖。

## 字段

### 基础字段

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| `schema_version` | integer | 当前固定为 `1` |
| `id` | string | `WDT-YYYYMMDDTHHMMSSZ-XXXXXX`，UTC 时间加 6 位随机十六进制 |
| `title` | string | 简短、可扫描的任务名称 |
| `repo` | string | 工作仓库，固定为 GitHub `owner/name` |
| `created_by` | identity | 添加任务的人类 GitHub 身份 |
| `owner` | identity 或 null | execute 后的责任人；不是 agent 身份 |
| `priority` | enum | `P0`、`P1`、`P2`、`P3`，数字越小越优先 |
| `created_at` | string | UTC RFC 3339，秒精度，必须以 `Z` 结尾 |
| `goal` | string | 期望产生的具体结果 |
| `context` | string[] | agent 开工所需的约束、背景、入口和非目标 |
| `acceptance_criteria` | criterion[] | 至少一条可验证的完成条件 |
| `dependencies` | string[] | 其他 WudiTask ID；不复制其仓库或验收内容 |
| `claim` | claim 或 null | execute 的并发确认记录 |
| `links` | string[] | canonical PR/Issue、设计稿或文档链接；有明确归属仓库时优先包含 Issue/PR URL |
| `completion` | completion | 仅 archive 文件存在 |

### Identity

```json
{
  "login": "octocat",
  "github_id": 583231
}
```

`github_id` 是 GitHub numeric ID，比可改名的 login 更稳定。运行远端写操作时，工具始终通过 `gh api user` 获取身份。系统没有 `agent_owner` 字段。

### Acceptance criterion

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

`verification.type` 只能是：

- `command`：在工作仓库执行的确定性命令。
- `file`：应检查的文件路径与期望内容。
- `url`：应检查的 HTTP URL 与期望结果。
- `manual`：必须由人或 agent 进行的明确观察。

`description` 说明“什么算完成”，`verification` 说明“怎样证明”。两者都不能为空。

### Dependency

`dependencies` 只存任务 ID：

```json
"dependencies": [
  "WDT-20260710T080000Z-12AB34"
]
```

被引用任务是其 `repo`、`goal` 和 `acceptance_criteria` 的唯一事实源。`dep-check` 会实时展开这些内容。这样修改依赖任务时，不会在所有下游文件中留下过期副本。

添加任务时依赖 ID 必须已存在。校验器仍会检查手工编辑、错误 merge 或历史导入造成的缺失依赖和环。

### Claim

```json
{
  "token": "d7c3f832d0644bbca8f43fb48d2f53ae",
  "github_login": "octocat",
  "github_id": 583231,
  "claimed_at": "2026-07-11T12:04:10Z"
}
```

`token` 是每次领取生成的随机 nonce，用于识别一次 claim；它不是 agent 身份或秘密。owner 与 claim 中的 GitHub identity 必须一致。

### Completion

```json
{
  "outcome": "done",
  "completed_at": "2026-07-11T13:20:00Z",
  "completed_by": {
    "login": "octocat",
    "github_id": 583231
  },
  "result": "Validation added and regression tests pass",
  "acceptance_results": [
    {
      "criterion_id": "AC-1",
      "status": "passed",
      "evidence": "python3 -m unittest tests.test_upload: 12 tests passed"
    }
  ]
}
```

`outcome=done` 时，每个 criterion 必须恰好有一个 `passed` 结果和非空 evidence。`failed` 或 `cancelled` 会永久保存记录，但不会让依赖它的任务变为 ready。

## 完整 open 示例

```json
{
  "schema_version": 1,
  "id": "WDT-20260711T120000Z-A1B2C3",
  "title": "Harden upload validation",
  "repo": "acme/api",
  "created_by": {
    "login": "octocat",
    "github_id": 583231
  },
  "owner": null,
  "priority": "P1",
  "created_at": "2026-07-11T12:00:00Z",
  "goal": "Reject malformed uploads before object storage",
  "context": [
    "Preserve the current public API",
    "The upload entry point is src/upload.py"
  ],
  "acceptance_criteria": [
    {
      "id": "AC-1",
      "description": "Malformed files return HTTP 400",
      "verification": {
        "type": "command",
        "value": "python3 -m unittest tests.test_upload"
      }
    }
  ],
  "dependencies": [
    "WDT-20260710T080000Z-12AB34"
  ],
  "claim": null,
  "links": [
    "https://github.com/acme/api/issues/42"
  ]
}
```

## 信息充分性

`add` 至少需要 title、repo、goal 和一条 acceptance criterion。缺失时不会猜测，而是返回：

```json
{
  "ok": false,
  "error": {
    "code": "insufficient_task_spec",
    "message": "The task needs more information before it can be added.",
    "details": {
      "missing": ["acceptance_criteria"],
      "questions": ["What observable checks prove this task is complete?"]
    }
  }
}
```

agent skill 应把 `questions` 原样或等价地询问用户，获得答案后再执行 add。

### Issue/PR 与任务文本的边界

有明确归属 GitHub 仓库的工作，以已有匹配 open PR、已有匹配 open Issue 或在该仓新建的 Issue 作为完整问题叙述，并把规范 URL 放入 `links`。WudiTask 的 `goal`、`context` 与 `acceptance_criteria` 是精简执行合同，不复制整篇 Issue/PR 正文，避免两份叙述漂移。

没有合适仓库承载 Issue/PR 时，完整叙述才保留在 WudiTask 文本字段中。schema v1 的 `repo` 仍表示实际执行仓库且为必填项；agent 不得虚构仓库，也不得借用 Task Hub 的 Issue 描述其他仓库工作。

## 修改格式

普通参与者不应直接编辑 JSON，必须使用 `wuditask add/execute/archive/release`。格式升级流程是：

1. 更新 schema 与本文档。
2. 更新 Python validator 和迁移脚本。
3. 增加覆盖旧版本与新版本的测试。
4. 在一个提交中迁移数据。
5. 运行 `python3 tools/wuditask.py --local validate` 后才能合并。
