# 分布式协作工作流

本文档描述组织内从部署、添加、领取、执行、依赖检查到归档的完整流程。所有参与者遵循同一个原则：**GitHub 远端是唯一事实源，Pages 只读，agent 只通过 Python CLI 改任务。**

## 角色

| 角色 | 责任 |
| --- | --- |
| Task Hub maintainer | 维护 schema、Python 工具、skills、Pages workflow 与默认分支策略 |
| Task author | 提供足够的目标、上下文、目标仓库、验收标准与依赖 |
| Human owner | 由 `gh` 识别，对领取的任务负责；可以让多个 agent 协作 |
| Agent | 调用 skill 与 CLI、修改工作仓库、执行验收、提交证据；不是任务 owner |
| Reviewer | 根据 acceptance/evidence 检查工作结果，可与 owner 是同一人 |

## 0. 建立 Task Hub

1. 创建 GitHub 仓库，首次演练可使用 private。
2. 推送本仓库内容到默认分支 `main`。
3. 配置协作者普通 push 权限，并明确禁止 force push 与删除默认分支。
4. 确认当前方案允许从该仓库发布 Pages；GitHub Free 的个人 private 仓不允许。
5. 在 Settings > Pages 中选择 GitHub Actions，并创建仓库变量 `WUDITASK_PAGES_ENABLED=true`。
6. 确认 Pages 的实际可见性；若没有 Enterprise 私有 Pages，使用脱敏任务。
7. 等待 `Deploy WudiTask Pages` 成功。

未设置变量时，workflow 仍执行 schema 校验、测试和静态构建，但跳过 Pages 上传与部署。这是 private/Free 演练的正常模式。

部署验收：

```bash
python3 tools/wuditask.py --local validate
python3 tools/wuditask.py --local build-site --output _site
python3 -m unittest discover -s tests -v
```

## 1. 每台机器注册访问

用户克隆 Task Hub 到任意位置：

```bash
git clone git@github.com:ORG/wuditask.git ~/somewhere/wuditask
```

在该 clone 中：

- Codex：调用 `$wuditask-install`
- Claude Code：调用 `/wuditask-install`

install skill 调用同仓 Python：

```bash
python3 tools/wuditask.py --json install
```

安装结果只做路径注册与 symlink，不执行 pip/npm 安装，也不复制 skill。installer 要求 `.agents/skills/` 恰好包含当前九项 skill，缺少或多出都拒绝安装，然后把完整 suite 分别链接给 Codex 与 Claude。clone 保持原路径时，既有 skill 内容随更新立即生效；只有更新改变 skill 目录清单、clone 移动或链接损坏时才需要重新运行幂等 install。若目标 skill 路径已有其他内容，默认停止；只有用户确认后才使用 `--replace`，旧内容会改名保留为 backup。

### 更新 WudiTask 本体

`/wuditask-selfupdate`（Codex 为 `$wuditask-selfupdate`）用于升级已安装 clone。CLI 先 fetch 并检查 Git 关系；只有 worktree 干净且本地可 fast-forward 时，才在临时 clone 中校验候选数据并运行完整测试，随后 `merge --ff-only`。dirty、local-ahead、diverged 或候选测试失败都保持当前版本不变，且不会自动 stash/reset/rebase。`--check` 只报告，`reinstall_required_after_update=true` 仅预告更新后的链接同步。非 check 结果为 `updated`/`up_to_date` 且 `reinstall_required=true` 时，skill 无 `--replace` 地运行一次 installer；它补齐链接，并只删除仍指向本 clone 的过期 skill symlink，遇到其他目标冲突则停止而不覆盖。

若用户在另一个工作仓发现 WudiTask 缺陷，使用 `/wuditask-selfupdate fix <问题>`（Codex 为 `$wuditask-selfupdate fix <问题>`）。agent 保持原仓现场与活动任务状态不变，更新 installed clone，然后从 `origin/main` 创建 `~/.wuditask/worktrees/<unique-slug>` 和 `codex/<slug>` 隔离实现。修复通过完整测试后普通 push 到 main；main 移动时只 rebase agent 创建的分支并重测，绝不 force-push。没有直推权限或需要评审时才推分支并创建 PR。变更进入 main 后，agent 更新 installed clone、按需同步 skill 链接、清理 worktree，再返回原仓。

这个 fix 是直接仓库维护，不是共享任务：不得为了描述它而创建 GitHub Issue，也不得调用 WudiTask `add`、`execute`、`archive` 或 `release`。`fix` 是 agent 工作流关键词，不是 CLI 参数。

## 2. 添加任务

用户可以在任意工作仓库中告诉 agent：

> 添加一个任务：上传接口必须在写对象存储前拒绝格式错误的文件。

agent 使用 `$wuditask-add`（Claude 为 `/wuditask-add`）：

1. 读取当前工作仓库 `git remote get-url origin`。
2. 先收集完整问题叙述，以及精简的 title、goal、context、acceptance criteria、verification、priority 与 dependencies；不清楚时先询问用户，不凭空定义“完成”。
3. 只有叙述和执行合同充分后，才在明确的归属仓库复用匹配的 open PR/open Issue，或按该仓 Issue template 创建 Issue，并把 URL 放入 `links`。
4. 以 Issue/PR 作为完整问题叙述，WudiTask 字段只保留精简执行合同。
5. 调用 `add --json`，只有普通 push 确认后才向用户报告任务 ID。

不得在 Task Hub 创建 Issue 来描述属于其他仓库的工作，也不得创建空 PR 充当说明。归属仓库适合承载 Issue 但创建失败时应停止并报告，不能静默降级为文本。只有确实没有合适 GitHub 仓库承载叙述时，才在 WudiTask 文本字段保留完整描述并在 `context` 记录原因；schema v1 仍要求一个实际执行仓库，不能虚构。

最低信息：

- title：人可扫描的名称。
- repo：唯一的 GitHub `owner/name`。
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
   - owner 与 claim 都为空；
   - 所有依赖已满足；
   - 尚未归档。
5. CLI 写入 human owner 和 claim，普通 push。
6. agent 仅在 `ok=true`、`confirmed=true`、`sync.confirmed=true` 后开始修改工作仓库。

显式领取：

```bash
wuditask execute WDT-20260711T120000Z-A1B2C3
```

自动领取：

```bash
wuditask execute
```

同一任务被两台机器竞争时，只有一个普通 push 能基于当时的 branch head 成功。失败方从新快照重试后看到 owner，返回 `claim_conflict`。如果远端变化属于另一个任务，工具会自动重放并继续。

### 执行中

agent 应把 task 的以下内容当作执行合同：

- `repo`：只能在这个工作仓库实施。
- `goal`：最终结果。
- `context`：约束、入口与非目标。
- `acceptance_criteria`：完成前逐项验证。
- `links`：外部上下文。

如果开工后发现信息仍不足：

1. 暂停不可逆修改。
2. 向用户提出具体问题。
3. 信息澄清后继续。
4. 若不应继续，调用 `release --reason` 返回队列；不要删除任务或手工清空 owner。

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

## 5. 验收与归档

agent 在工作仓库完成实现后，使用 `$wuditask-archive`（Claude 为 `/wuditask-archive`）：

1. 逐条执行 task 中的 verification。
2. 保留可复核证据，例如命令、测试数量、commit/PR、文件路径或人工观察。
3. 确认代码已经按团队流程提交/推送到工作仓库。
4. 调用 archive。

```bash
wuditask archive WDT-20260711T120000Z-A1B2C3 \
  --outcome done \
  --result "Malformed uploads now fail before storage" \
  --evidence "AC-1=python3 -m unittest tests.test_upload: 12 passed" \
  --evidence "AC-2=PR https://github.com/acme/api/pull/88 reviewed"
```

`done` 缺任意 criterion 的 evidence 时，CLI 返回 `insufficient_archive_evidence`，任务保持 open/in_progress。

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

## 6. 释放任务

使用 `$wuditask-release`（Claude 为 `/wuditask-release`）。

owner 暂时无法继续、任务领错仓库或需要重新排队时：

```bash
wuditask release TASK_ID --reason "Waiting for product decision"
```

release 只允许当前 GitHub owner 操作。原因进入命令结果与 Git commit message；任务文件恢复为 owner/claim 均为空。长期审计由 Git 历史保留。

## 7. 列出任务

`$wuditask-list`（Claude 为 `/wuditask-list`）只运行 `list`，用于列出或筛选 open、archive 或 all 范围内的任务，不修改任务。

## 8. 查看单个任务

`$wuditask-show`（Claude 为 `/wuditask-show`）只运行 `show TASK_ID`，用于查看一个任务的完整字段和派生依赖状态，不修改任务。更深入的依赖就绪性分析交给 `wuditask-dep-check`。

## 9. Pages 使用

人类打开 GitHub Pages 可以：

- 查看 ready、in progress、blocked 与 archived 数量；
- 按仓库、状态和关键词过滤；
- 展开 goal、context、验收标准、owner 和依赖；
- 检查归档 outcome 与 evidence。

Pages 不提供 Add/Execute/Archive 按钮，这是有意的：所有写操作必须经过身份、依赖、schema 与普通 push 协议。

## 8. 故障处理

### `claim_conflict`

目标任务已被另一 GitHub owner 领取。agent 不工作，改为执行不带 ID 的 execute 领取下一项，或等待用户指示。

### `no_ready_task`

当前仓库没有可领取任务。查看返回的 blockers，再运行 dep-check；不要绕过依赖。

### `push_status_unknown`

网络或认证错误导致服务端结果不明确。CLI 会先尝试从远端重新读取并对比完整任务；若一致则返回 `confirmation=remote_reconciliation`。仍无法确认时，agent fail closed，不开始/不宣布完成；使用 `error.details.task_id` 重试显式命令。execute 必须改成 `execute TASK_ID`，避免恢复时领取第二项。若远端已经接受，add/execute/archive 会从最新快照返回幂等确认。

### `concurrent_update_exhausted`

短时间内远端持续变化超过重试次数。稍后重试。禁止用 force push“解决”。

### Pages 构建失败

先看 Actions 的 validate 步骤。修复 JSON/schema/依赖错误后普通 push。Pages 故障不改变 Git 中的任务事实。

## 9. 团队治理

- task author 对信息充分性负责。
- owner 对执行与证据真实性负责。
- maintainer 对 schema、CLI 与 skills 的当前契约一致性负责。
- 任何参与者都不得直接删除 archive。
- 任何参与者都不得 force-push Task Hub。
- 敏感信息不得写入会公开发布的 Pages 数据。
- 定期审查长期 in-progress 任务；由原 owner release，或通过明确的维护流程处理。
