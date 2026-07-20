# 分布式协作工作流

WudiTask 把 GitHub 责任人与 Hub agent execution 分开。canonical Issue/PR 是
任务合同；Hub 只保存 priority、跨仓依赖、active runs 与归档结果。

## 角色

| 角色 | 职责 |
| --- | --- |
| Requester | 在 canonical Issue/PR 写清目标、约束与 acceptance |
| GitHub owner | PR author/assignee，或 Issue assignee/closing-linked PR author |
| Active agent | Hub 中一个 `{login,run_id}` 执行记录 |
| Task Hub maintainer | 维护独立 Hub remote、普通 push 权限和 Pages |
| WudiTask maintainer | 维护 CLI、schema、十二个 skills 与测试 |

一个任务可以有多个 owners 和多个不同 login 的 active agents。assignment 不
等于 execute。execute 选择 unowned task 时会先把当前 login self-assign 并确认，
随后才独立启动 Hub run；这不是一个跨系统原子事务。

## 0. 建立 Hub 与 Pages

Hub 是独立 Git 仓，根目录 `hub.json` 必须为 task schema 3 / tool API 5。
默认分支保存 `data/open`、`data/archive` 和 `data/deletions`，只允许普通 push。
Pages workflow 固定一个完整工具 commit SHA，用它 validate 并生成 snapshot
schema v3；Pages 永远只读。

Hub Issue 可以在执行仓无法承载 Issue 时作为 canonical fallback。Issue form 必须
把执行仓、完整 narrative、acceptance 和依赖写入 Issue body；不存在 text task。

## 1. 每台机器注册访问

使用 `$wuditask-install`（Claude 为 `/wuditask-install`）注册工具 clone 与独立
Hub remote：

```bash
python3 tools/wuditask.py --json install \
  --hub-remote https://github.com/OWNER/wuditask-hub.git \
  --hub-branch main
```

installer 校验精确的十二项 skill，并链接 add、archive、assign、check、delete、
execute、install、list、release、selfupdate、show、unassign。它会安全移除仍指向
本 clone 的旧 dep-check/reconcile symlink，不删除个人文件。

Hub bare cache 位于 `$XDG_CACHE_HOME/wuditask` 或 `~/.cache/wuditask`。每条命令
使用独立 operation worktree；cache 可重建，不是事实源。

### 更新工具

```bash
wuditask selfupdate --check
wuditask selfupdate
```

skill inventory 改变时，non-check update 返回 `reinstall_required=true`；随后无
`--replace` 地重跑 install。直接维护工具使用 `$wuditask-selfupdate fix`，在
`~/.wuditask/worktrees/<slug>` 隔离修改，不创建 Issue 或队列任务。Hub 永不
force-push；工具仓只有在明确授权并带精确旧 OID 的 `--force-with-lease` 时允许
改写 agent 自有历史。

## 2. 先建立 source，再 add

`$wuditask-add` 按顺序复用执行仓 PR、复用/创建执行仓 Issue、或在确有原因时
创建 Hub fallback Issue。source 必须先包含完整目标、context、constraints、
acceptance 与必要链接。

```bash
wuditask add \
  --repo acme/api \
  --source https://github.com/acme/api/issues/42 \
  --priority P1 \
  --depends WDT-20260711T120000Z-A1B2C3
```

Hub 只写最小协调字段，不复制 title/body/acceptance。临时认证或网络失败必须
报错，不能创建 text source。只有 `ok=true`、`confirmed=true` 与
`sync.confirmed=true` 同时成立才报告 task ID。

## 3. assign 与 unassign

assignment 只修改 canonical GitHub source，不写 Hub。

```bash
wuditask assign TASK_ID
wuditask assign TASK_ID --to other-login

wuditask unassign TASK_ID
wuditask unassign TASK_ID --from other-login
```

默认目标是当前认证 login。`--to`/`--from` 指向他人时必须有用户对该具体 login
的明确授权，不能由 agent 推断。GitHub repository permissions 仍是最终 guard。

PR owners 是 author + PR assignees；Issue owners 是 Issue assignees +
closing-linked PR authors。普通 timeline mention 不产生 owner。移除 assignee 不会
移除 authorship，因此 unassign 后目标可能仍是 owner。
若该 login 仍有 active run，unassign 拒绝并要求先逐个 release；它绝不把停止
执行隐含进 GitHub 操作。

## 4. 统一 check

`$wuditask-check` 是唯一的依赖与协调检查入口：

```bash
wuditask --json check [TASK_ID]
```

它同时报告：

- dependency closure、blockers 与 ready；
- GitHub owners、Issue/PR delivery、reviews 与 checks；
- Hub active-agent login/run_id；
- active agent 不再是 owner、terminal task 待 archive、archive/source mismatch；
- GitHub unavailable 等未知状态。

check 纯读。旧 `dep-check` 与 `reconcile` 命令和 skills 已删除，没有 alias。
GitHub merge/close 不直接解除依赖；只有 WudiTask archive `done` 才解除。

## 5. execute agent run

从任务的执行仓运行：

```bash
wuditask execute [TASK_ID]
```

无 ID 时先选 assigned-to-current-login 且 idle 的 ready task，再选 unowned ready
task；自动选择不会采用只由其他人拥有的工作。显式 task ID 表示用户选择加入该
任务：若当前 login 尚非 owner，CLI 会先用 GitHub assignee API 把它添加为
co-owner，不移除已有 owners。CLI 检查 execution repo、dependencies 与 live
delivery，重新读取 owners 确认后才生成新的 `run_id`，并以另一笔普通 Hub push
添加：

```json
{"login": "alice", "run_id": "WDX-0123456789ABCDEF01234567"}
```

不同 login 可以同时执行。同一 login 最多一个 active entry；已有 entry 返回
`active_agent_conflict`，不能覆盖。Hub push 后再次读取 GitHub；若 owner/delivery
竞态使启动不再合法，只按新 run_id 补偿删除本次 entry。

self-assignment 和 Hub start 是两次明确事务，不伪装成原子操作。assignment 成功
但 Hub start 失败或被补偿时，GitHub assignment 保留，CLI 明确报告没有 run
启动；execute 不自动回滚 assignment。

agent 只有在 `ok=true`、`confirmed=true`、`sync.confirmed=true`、
`work_authorized=true` 且返回 run_id 后开工。保存 run_id，release/archive 都必须
精确匹配它。

## 6. release 一个 run

```bash
wuditask release TASK_ID \
  --run-id RUN_ID \
  --reason "Waiting for product decision"
```

release 只删除当前 authenticated login 的 matching run_id，不 unassign GitHub，
不停止其他 login，也不能用旧 run_id 删除同一 login 的新 run。即使 GitHub
unavailable 或外部已 unassign，release 仍可清理 Hub 执行状态。

`agent_not_active` 或 `active_agent_run_mismatch` 必须停止；不要手改 JSON。

## 7. acceptance 与 archive

acceptance 只读 canonical Issue/PR。完成 source 中的验证、提交具体 evidence，
并确认 Issue completed 或 PR merged：

```bash
wuditask archive TASK_ID \
  --run-id RUN_ID \
  --outcome done \
  --result "Implemented and verified" \
  --evidence "python3 -m unittest: 12 passed" \
  --evidence "Merged PR: https://github.com/acme/api/pull/88"
```

done 要求至少一条 evidence、dependencies ready 和 GitHub 成功终态。存在 active
agents 时，调用者必须传自己的 matching `--run-id` 且仍是 live owner；archive
保存全部 participants 并清空全集。没有 active agent 时，只有 authenticated
`created_by` 可以显式归档任何匹配的终态 outcome，并且必须省略 `--run-id`；该
路径的 participants 为空，也覆盖在 WudiTask execute 之外完成的交付。

failed/cancelled 要求具体结果且不解除依赖；它们不受 dependency blocker 阻止，
但必须对应 GitHub 明确终态。旧 run ID 会被拒绝而不是忽略。NOT_PLANNED 不能
归档为 done。GitHub unavailable 时任何 outcome 都 fail closed。每次 archive
仍通过一个普通 Hub commit 原子移动文件、清空 active_agents 并保存 completion。

## 8. list、show 与 Pages

`$wuditask-list` 负责 scope/repo 过滤；`$wuditask-show` 展示一个 task；更深的
依赖和 drift 使用 check。

Pages 的 Tasks 页面分开显示 live owners 与 active-agent logins，不发布 run_id；
Dependencies 页面按全部/单仓显示 DAG；Install 与 Workflow 页面说明加入和操作
流程。snapshot 中 source body、owners、evidence 都可能公开，private source 要
配置最小只读 token，并把 unavailable 保留为未知。

## 9. 删除明确误建的 archive

`$wuditask-delete` 只用于用户明确指出的误建、重复或测试 archive：

```bash
wuditask delete TASK_ID [TASK_ID ...] --reason "Created by mistake"
```

完整批次必须没有批次外反向依赖。一个 Hub commit 删除记录并写持久 deletion
receipt；ID 永久保留。delete 不改 GitHub source，也不清除 Git 历史、旧 clone
或 Pages artifact，因此不是隐私擦除。

## 10. 常见故障

- `dependency_blocked`：运行 check，完成并 done-archive blockers。
- `github_delivery_unavailable`：状态未知；不 assign/execute/done archive。
- `delivery_owner_required`：任务由他人负责；显式 assign 或选择别的任务。
- `delivery_not_executable`：source 已 terminal 或当前状态不允许启动。
- `active_agent_conflict`：同 login 已有 run；继续该 run 或先按其 run_id release。
- `active_agent_run_mismatch`：参数是旧 run；不得重试为“当前任意 run”。
- `insufficient_archive_evidence`：补充 source-defined acceptance 的具体证据。
- `push_status_unknown`：不要开工。先用 check 查找错误详情中的精确 run_id；
  若它已出现，先按该 run_id release，再重新 execute；若未出现，直接重新
  execute。不要把“状态未知”当成成功。

## 11. 团队治理

- GitHub owners 负责业务交付；每个 active agent 只对自己的 run 负责。
- 多 agent 协作是集合并发，不会形成单人独占。
- 默认分支禁止 force push；Hub 写入必须能由普通 push 确认。
- 定期 check active agent 与 owner drift、terminal 未 archive 和长期 blocker。
- source 先于 queue entry，acceptance 与讨论只维护在一个 Issue/PR。
