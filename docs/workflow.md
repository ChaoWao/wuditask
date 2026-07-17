# 分布式协作工作流

本文档描述从添加、领取、交付到归档的完整流程。GitHub Issue/PR 是描述、
责任人与交付进展的事实源；独立 Task Hub 是执行租约、跨仓依赖、验收证据与
归档结果的事实源。Pages 只读，agent 只通过 CLI 改任务数据。

## 角色

| 角色 | 责任 |
| --- | --- |
| Tool maintainer | 维护 schema、Python 工具、skills、dashboard 与工具发布 |
| Task Hub maintainer | 维护 `hub.json`、任务数据、Pages workflow 与默认分支策略 |
| Task author | 提供足够的目标、上下文、目标仓库、验收标准与依赖 |
| GitHub assignee | 对 Issue 的业务交付负责，可与 agent 协作 |
| Claim holder | 由 `gh` 识别，持有 WudiTask 独占执行租约 |
| Agent | 调用 skill 与 CLI、修改工作仓库、执行验收、提交证据；不是 owner |
| Reviewer | 根据 GitHub review 与 WudiTask evidence 检查结果 |

## 0. 建立 Task Hub

1. 创建独立 Task Hub GitHub 仓库，首次演练可使用 private。
2. 写入严格的 `hub.json`、`data/open`、`data/archive` 和 Pages workflow。
3. Pages workflow 固定 checkout 一个 WudiTask 工具完整 commit SHA。
4. 配置协作者普通 push 权限，并通过 GitHub ruleset 的
   `non_fast_forward` 与 `deletion` 规则禁止 force push 和删除默认分支。
5. 确认当前方案允许从该仓库发布 Pages；GitHub Free 的个人 private 仓不允许。
6. 在 Settings > Pages 中选择 GitHub Actions，并创建仓库变量 `WUDITASK_PAGES_ENABLED=true`。
7. 确认 Pages 的实际可见性；若没有 Enterprise 私有 Pages，使用脱敏任务。
8. 等待 `Deploy WudiTask Pages` 成功。

Hub 同时启用 Issues，并安装 `.github/ISSUE_TEMPLATE/fallback-task.yml`。Issue
属于 GitHub 元数据，不修改 Hub 数据分支，因此不会与 task JSON push 冲突。
跨仓私有 source 需要配置只读 `WUDITASK_GITHUB_TOKEN`，其最小访问范围覆盖仓库
元数据、Issues、pull requests、checks 与 commit statuses。

未设置变量时，Hub workflow 仍执行 schema 校验和静态构建，但跳过 Pages
上传与部署。工具测试只由工具仓 CI 执行。

部署验收：

```bash
python3 TOOL/tools/wuditask.py --hub HUB --local validate
python3 TOOL/tools/wuditask.py --hub HUB --local build-site --output _site
cd TOOL && python3 -m unittest discover -s tests -v
```

## 1. 每台机器注册访问

用户只需克隆工具仓到任意位置，并知道独立 Task Hub 的 Git remote：

```bash
git clone git@github.com:ORG/wuditask.git ~/somewhere/wuditask
```

在该 clone 中：

- Codex：调用 `$wuditask-install`
- Claude Code：调用 `/wuditask-install`

install skill 调用工具仓 Python，并显式配置 Hub：

```bash
python3 tools/wuditask.py --json install \
  --hub-remote https://github.com/ORG/wuditask-hub.git \
  --hub-branch main
```

安装结果把工具路径与 Hub 远端分别写入 config schema v2，并创建 symlink，
不执行 pip/npm 安装，也不复制 skill。installer 在用户 cache 中初始化或复用
Hub bare repository，fetch 配置分支，并在隔离 worktree 中校验 `hub.json` 和
任务数据。校验失败时不会写 config、skill link 或 launcher；已经取得的 Git
objects 可以作为可删除 cache 保留。旧 `hub_path` 配置不兼容，也不会自动迁移。

cache 默认位于 `~/.cache/wuditask`；绝对路径的 `XDG_CACHE_HOME` 会覆盖
`~/.cache`。`hubs/` 按 remote 和 branch 的哈希分桶，`operations/` 保存每条
命令的唯一 worktree，`locks/` 只协调本机 cache 的 fetch 与 worktree 元数据。
每个 operation 另持有独立 lease。正常完成时立即删除 worktree；若进程被强杀，
下一条命令会回收已经无人持有 lease 的 orphan。bare repository 长期复用。
cache 路径不进入 config，install JSON 通过 `hub_cache` 单独报告它。

### 更新 WudiTask 本体

`/wuditask-selfupdate`（Codex 为 `$wuditask-selfupdate`）只升级配置中的
工具 clone。CLI fetch `tool_remote/tool_branch`，在临时工具 clone 中运行
完整工具测试后 `merge --ff-only`。它不读取或验证 live Hub，Hub 任务提交
不会产生工具更新。dirty、local-ahead、diverged 或候选测试失败都保持当前
工具版本不变，且不会自动 stash/reset/rebase。

若用户在另一个工作仓发现 WudiTask 缺陷，使用
`/wuditask-selfupdate fix <问题>`（Codex 为
`$wuditask-selfupdate fix <问题>`）。agent 从配置的工具远端和分支创建
隔离 worktree；直接维护不会在 Hub 中添加、领取或归档任务。

这个 fix 是直接仓库维护，不是共享任务：不得为了描述它而创建 GitHub Issue，也不得调用 WudiTask `add`、`execute`、`archive` 或 `release`。`fix` 是 agent 工作流关键词，不是 CLI 参数。

工具仓与 Task Hub 的历史策略不同。direct fix 默认先普通 push；agent 自有工具
分支经过 rebase/amend，或用户明确要求改写配置的工具分支时，可以对精确匹配
配置的 `tool_remote` 使用
`--force-with-lease=refs/heads/<branch>:<observed-oid>`。必须先 fetch、记录旧
OID、审查被替换提交并重跑完整测试；lease 失败后停止检查新提交，不能刷新
expected OID 后自动重试。禁止裸 `--force`、裸 `--force-with-lease`，也禁止
将这个例外用于 `hub_remote`、他人的分支、tag 或 release。SSH/HTTPS remote
必须先归一为 canonical `owner/name`；无法证明目标就是 `tool_remote` 且不同于
`hub_remote` 时 fail closed。若配置的工具分支被改写，普通 selfupdate 会报告
diverged；必须另行取得明确授权才能 reset、替换或重新 clone 本地安装。

## 2. 添加任务

用户可以在任意工作仓库中告诉 agent：

> 添加一个任务：上传接口必须在写对象存储前拒绝格式错误的文件。

agent 使用 `$wuditask-add`（Claude 为 `/wuditask-add`）：

1. 读取当前工作仓库 `git remote get-url origin`。
2. 先收集完整问题叙述，以及精简的 title、goal、context、acceptance criteria、verification、priority 与 dependencies；不清楚时先询问用户，不凭空定义“完成”。
3. 在执行仓复用匹配 PR/Issue，或按该仓模板创建 Issue。
4. 执行仓无法承载时，从 config 的 `hub_remote` 找到 Hub，使用 fallback Issue
   form，并记录 `fallback_reason`。两边都不能承载时才使用解释过的 text source。
5. 将 canonical Issue/PR 写入结构化 `source`；Hub fallback 使用独立的
   `github_issue_fallback` kind，`links` 只保留辅助资料。
6. WudiTask 只保留精简执行合同，不复制完整正文或 GitHub 可变进展。
7. CLI 确认 source 存在且可读；跨仓 source 只接受配置 Hub 的 fallback Issue。
8. CLI 从 config 的 `hub_remote/hub_branch` 写入，普通 push 确认后才报告 ID。

Hub Issue 是正式 fallback，不是默认入口。目标仓适合承载时仍必须优先放在目标
仓；暂时的网络或认证错误不能静默降级成 text。不得创建空 PR 充当说明。

最低信息：

- title：人可扫描的名称。
- repo：唯一的 GitHub `owner/name`。
- source：唯一 canonical Issue、PR，或解释过的 text source。
- goal：期望结果，而不是笼统动作。
- acceptance：至少一条可观察结果，最好带 command/file/url。

依赖必须使用已经存在的 WudiTask ID。每个依赖任务自身携带仓库、目标和验收标准，父任务不复制。

若 CLI 返回 `insufficient_task_spec`，agent 读取 `details.questions`，向用户补问后重试。若返回 `missing_dependency`，先添加依赖任务或纠正 ID。

## 3. 领取并执行任务

用户进入某个工作仓库并要求 agent 处理下一项任务。`$wuditask-execute`（Claude 为 `/wuditask-execute`）执行：

1. 确认当前仓库没有会被意外覆盖的本地工作。
2. 调用 `wuditask execute`，不传 ID 时按 P0 到 P3、创建时间、ID 排序。
3. CLI 使用 `gh api user` 获取 login 与 numeric ID。
4. CLI 从远端新快照中筛选：
   - `repo` 等于当前工作仓库；
   - claim 为空；
   - 所有依赖已满足；
   - 尚未归档。
5. CLI 实时查询 source，只允许未开始、由当前用户负责或显式待验收的任务。
6. CLI 写入 claim 并普通 push。未指派 Issue 随后指派当前用户；所有 GitHub
   source 都在 push 后重新检查 ownership。失败或竞态触发带 token 的补偿。
7. agent 仅在 `ok=true`、`confirmed=true`、`sync.confirmed=true` 且
   `work_authorized=true` 后开工。

显式领取：

```bash
wuditask execute WDT-20260711T120000Z-A1B2C3
```

自动领取：

```bash
wuditask execute
```

同一任务被两台机器竞争时，只有一个普通 push 能基于当时 branch head 成功。
失败方从新快照看到 claim，返回 `claim_conflict`。GitHub 与 Hub 无跨仓原子
事务，因此 execute 使用 post-push 二次读取和补偿 release；任何不确定性都
fail closed。显式领取已完成 delivery 只用于验收，返回
`work_authorized=false`。`--hub --local` 永不编辑真实 GitHub assignment。

### 执行中

agent 应把 task 的以下内容当作执行合同：

- `repo`：只能在这个工作仓库实施。
- `goal`：最终结果。
- `context`：约束、入口与非目标。
- `acceptance_criteria`：完成前逐项验证。
- `source`：完整问题叙述及实时交付进展。
- `links`：辅助上下文。

如果开工后发现信息仍不足：

1. 暂停不可逆修改。
2. 向用户提出具体问题。
3. 信息澄清后继续。
4. 若不应继续，调用 `release --reason` 返回队列；不要删除任务或手工清空 claim。

## 4. 依赖检查

使用 `$wuditask-dep-check`（Claude 为 `/wuditask-dep-check`）。这是纯读工作流。

依赖检查有四层：

1. `add`：拒绝不存在的依赖 ID。
2. `execute`：领取前强制实时 dep-check。
3. `archive done`：归档前再次检查，避免绕过阻塞。
4. GitHub Actions/Pages：任务提交触发构建，并每小时安全刷新；浏览器每 60 秒刷新 snapshot。

手工检查所有任务：

```bash
wuditask --json dep-check
```

检查单个任务：

```bash
wuditask --json dep-check WDT-20260711T120000Z-A1B2C3
```

每个依赖会展开：

- task ID 与是否存在；
- 工作仓库、title、goal；
- acceptance criteria；
- open/archive 位置；
- completion outcome 与逐条 evidence；
- 未就绪原因。

依赖只有在“已 archive + outcome done + 所有验收 passed 且 evidence 非空”时完成。仍 open、failed、cancelled、缺证据、缺任务或成环都会阻塞。

GitHub merge/close 只把 delivery 推进到 `verification_needed`，不会直接解除
依赖。使用 `$wuditask-reconcile`（Claude 为 `/wuditask-reconcile`）对照实时
delivery 与 coordination：

```bash
wuditask --json reconcile
wuditask --json reconcile WDT-20260711T120000Z-A1B2C3
```

## 5. 验收与归档

agent 在工作仓库完成实现后，使用 `$wuditask-archive`（Claude 为 `/wuditask-archive`）：

1. 确认 canonical Issue 当前为 completed（通常由 closing PR merge 触发），或
   canonical PR 已 merged；open/reopened Issue 仍是 active。
2. 逐条执行 task 中的 verification。
3. 保留可复核证据，例如命令、测试数量、commit/PR、文件路径或人工观察。
4. 确认代码已经按团队流程提交/推送到工作仓库。
5. 调用 archive。

```bash
wuditask archive WDT-20260711T120000Z-A1B2C3 \
  --outcome done \
  --result "Malformed uploads now fail before storage" \
  --evidence "AC-1=python3 -m unittest tests.test_upload: 12 passed" \
  --evidence "AC-2=PR https://github.com/acme/api/pull/88 reviewed"
```

`done` 缺任意 criterion 的 evidence 时，CLI 返回 `insufficient_archive_evidence`，任务保持 open/in_progress。
GitHub 尚未完成时返回 `github_delivery_incomplete`；GitHub 查询失败时 fail
closed。Issue 以 `NOT_PLANNED` 关闭只能归档为 `cancelled`，不能 `done`。

无法完成时仍归档保留历史：

```bash
wuditask archive TASK_ID \
  --outcome failed \
  --result "Upstream API cannot provide the required consistency guarantee"
```

或：

```bash
wuditask archive TASK_ID \
  --outcome cancelled \
  --result "Product requirement withdrawn"
```

failed/cancelled 永远不会解除下游依赖。作者应重新规划下游任务，而不是伪造 done。
未领取任务可以直接归档为 failed/cancelled，即使依赖 blocked；普通 Hub push
负责与并发 claim 原子竞争。若任务已有 claim，仍只允许 holder 归档。
GitHub-backed cancelled 必须先把 canonical Issue/PR 关闭为 not planned；failed
可以来自 not planned，也可以来自 delivery 完成后的 WudiTask 验收失败，但不能
在 delivery 仍活跃或未知时归档。text source 没有外部 terminal guard。

## 6. 释放任务

使用 `$wuditask-release`（Claude 为 `/wuditask-release`）。

claim holder 暂时无法继续、任务领错仓库或需要重新排队时：

```bash
wuditask release TASK_ID --reason "Waiting for product decision"
```

存在 claim 时，release 只允许当前 holder 操作；无 claim 的重试保持幂等。
GitHub Issue source 会先移除当前用户的
assignee、二次确认，再清 Hub claim；其他 assignee 不受影响。当前用户仍拥有
active closing PR 时不能宣称任务已回队列，需先关闭或转移该 PR。任一端失败都
fail closed；若 GitHub 已解除而 Hub push 未确认，lease 保持 locked/unknown，
必须重试 release 或 reconcile。原因进入结果与 Git commit message，长期审计
由 Git 历史保留。local mode 只改本地 Hub。

## 7. 列出任务

`$wuditask-list`（Claude 为 `/wuditask-list`）只运行 `list`，用于列出或筛选 open、archive 或 all 范围内的任务，不修改任务。

## 8. 查看单个任务

`$wuditask-show`（Claude 为 `/wuditask-show`）只运行 `show TASK_ID`，用于查看一个任务的完整字段和派生依赖状态，不修改任务。更深入的依赖就绪性分析交给 `wuditask-dep-check`。

## 9. Pages 使用

人类打开 GitHub Pages 可以：

- 查看 ready、in progress、blocked 与 archived 数量；
- 在当前 Open/Archive 页签内按实际存在的仓库、状态和关键词过滤；
- 分列查看 WudiTask queue state 与 GitHub delivery state；
- 展开 canonical source、assignees、closing PR、claim、验收标准和依赖；
- 检查归档 outcome 与 evidence。

Pages 不提供 Add/Execute/Archive 按钮，这是有意的：所有写操作必须经过身份、依赖、schema 与普通 push 协议。Hub workflow 固定工具版本以保证构建可复现；工具测试只在工具仓 CI 运行。

snapshot 还包含 source repo/URL、assignees、closing PR 作者/URL、review/check
摘要、delivery 时间与查询错误。把这些字段与 title、goal、context、claim、
evidence 一并视为对 Pages 读者可见；私有数据必须脱敏或使用受限 Pages。

## 10. 故障处理

### `claim_conflict`

目标任务已有另一 claim holder。agent 不工作，领取下一项或等待用户指示。

### `delivery_owned_elsewhere`

GitHub 已由其他 assignee 或 active closing-PR author 负责。不要建立第二套责任人。

### `github_delivery_unavailable`

GitHub 状态未知。读命令和 Pages 显示 unavailable；新的 execute 和 done archive
fail closed，不能把未知当作无人负责或已经完成。

### `no_ready_task`

当前仓库没有可领取任务。查看返回的 blockers，再运行 dep-check；不要绕过依赖。

### `push_status_unknown`

网络或认证错误导致服务端结果不明确。CLI 会先尝试从远端重新读取并对比完整任务；若一致则返回 `confirmation=remote_reconciliation`。仍无法确认时，agent fail closed，不开始/不宣布完成；使用 `error.details.task_id` 重试显式命令。execute 必须改成 `execute TASK_ID`，避免恢复时领取第二项。若远端已经接受，add/execute/archive 会从最新快照返回幂等确认。

### `concurrent_update_exhausted`

短时间内远端持续变化超过重试次数。稍后重试。禁止用 force push“解决”。

### Pages 构建失败

先看 Actions 的 validate 步骤。修复 JSON/schema/依赖错误后普通 push。Pages 故障不改变 Git 中的任务事实。

## 11. 团队治理

- task author 对信息充分性负责。
- GitHub assignee 对交付负责；claim holder 对执行租约与证据真实性负责。
- maintainer 对 schema、CLI 与 skills 的当前契约一致性负责。
- 任何参与者都不得直接删除 archive。
- 任何参与者都不得 force-push Task Hub。
- 工具仓维护者只在明确改写代码历史时使用带精确旧 OID 的
  `--force-with-lease`；该权限不传播到 Task Hub。
- 敏感信息不得写入会公开发布的 Pages 数据。
- 定期 reconcile 长期 in-progress 或外部 active delivery；由 claim holder release，
  或通过明确维护流程处理。
