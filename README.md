# WudiTask

WudiTask 是供人类与 AI agent 共同使用的分布式任务队列。canonical GitHub
Issue/PR 保存任务叙述、验收要求、owners 与交付进展；独立 Task Hub 只补充
跨仓依赖、当前 agent runs 和归档证据。它没有常驻服务器、数据库或包管理器
依赖。

## 系统边界

- **工具仓库**：保存 Python CLI、十二个 agent skills、schema、dashboard 源码与测试。
- **Task Hub**：独立 Git 仓库，保存严格版本的任务数据，并为无法使用业务仓
  Issue 的工作提供 fallback Issue tracker。
- **工作仓库**：agent 实际修改代码的任意 GitHub 仓库。
- **人类身份**：通过 `gh api user` 取得可读 GitHub login。
- **责任人与执行**：PR owners 是 author 与 PR assignees；Issue owners 是 Issue
  assignees 与 closing-linked PR authors。普通 timeline mention 不产生 owner。Hub
  的 `active_agents` 独立记录
  `{login, run_id}`；多个不同 login 可以同时执行。
- **Task Hub 并发协议**：只允许普通 push，绝不 force-push。写入失败后
  从远端新快照重试，并重新判断目标任务。
- **工具仓维护**：默认普通 push；明确改写工具代码历史时可以对
  `tool_remote` 使用带精确旧 SHA 的 `--force-with-lease`，但该例外永不适用于
  Task Hub。
- **网页视图**：GitHub Actions 校验数据并生成只读 GitHub Pages；网页不直接修改任务。

## 目录

```text
wuditask tool repository/
  wuditask/                          纯 Python 核心
  tools/wuditask.py                  统一入口
  site/                              静态 dashboard
  .agents/skills/                    Codex/Claude 共用 skills
  schemas/task.schema.json           公开任务 v3 契约
  schemas/deletion-receipt.schema.json 公开删除回执 v2 契约

wuditask-hub repository/
  hub.json                           task schema v3 / tool API v4 契约
  data/open/<task-id>.json           未归档任务
  data/archive/<year>/<task-id>.json 正常任务的归档记录
  data/deletions/<receipt-id>.json   持久删除回执与 ID 保留
  .github/workflows/pages.yml        固定工具版本后校验、构建、部署
```

## 快速开始

先克隆工具仓库，并准备一个独立的 Task Hub Git 远端：

```bash
git clone git@github.com:YOUR-ORG/wuditask.git
cd wuditask
```

然后在 Codex 中调用 `$wuditask-install`，或在 Claude Code 中调用 `/wuditask-install`。安装 skill 会：

1. 把当前工具 clone 及独立 Hub 远端写入 `~/.wuditask/config.json`。
2. 在用户 cache 中初始化或复用 Hub bare repository，并在隔离 worktree 中
   校验 `hub.json` 与任务数据。
3. 校验固定的十二项 WudiTask skill，并把它们链接到 `~/.agents/skills` 与
   `~/.claude/skills`；缺少或多出 skill 都会拒绝安装。
4. 把一个无安装包的启动链接放到 `~/.local/bin/wuditask`。

也可以直接运行：

```bash
python3 tools/wuditask.py --json install \
  --hub-remote https://github.com/YOUR-ORG/wuditask-hub.git \
  --hub-branch main
```

配置 schema v2 明确分开两套 Git 状态：

```json
{
  "schema_version": 2,
  "tool_path": "/absolute/path/to/wuditask",
  "tool_remote": "https://github.com/YOUR-ORG/wuditask.git",
  "tool_branch": "main",
  "hub_remote": "https://github.com/YOUR-ORG/wuditask-hub.git",
  "hub_branch": "main",
  "installed_at": "2026-07-11T12:00:00Z"
}
```

旧的 `hub_path` 配置不会被推断或迁移；重新运行 install 完成一次性切换。

Hub checkout 是可重建的派生数据，不写入 config。默认 cache 根目录是
`~/.cache/wuditask`；若 `XDG_CACHE_HOME` 是绝对路径，则使用
`$XDG_CACHE_HOME/wuditask`。布局如下：

```text
wuditask/
  hubs/<remote-and-branch-hash>.git  持久 bare repository
  operations/<unique-id>/hub        每次命令的隔离 worktree
  locks/<remote-and-branch-hash>.lock
  locks/operations/<unique-id>.lock operation lease
```

每个远端命令先 fetch 最新分支，再从精确 commit 创建 worktree。正常返回或
异常时都会删除 operation worktree，但保留 bare repository 和 Git objects。
每个 operation 在完整生命周期持有独立 lease；若进程被强杀或机器掉电，下一
条命令只回收已经能取得 lease 的 orphan worktree，避免误删仍活跃的并发操作。
不同 remote/branch 使用不同 hash，不会串用 origin；整个 cache 都可安全删除，
下一条命令会重建。install 的 JSON 结果通过 `hub_cache` 报告实际 bare cache
路径，配置 schema v2 不增加 cache 字段。

install 创建的是符号链接，不复制 skill 或 CLI：

- 两个 agent 目录下的 `wuditask*` skill 都指向工具 clone 中对应的 skill 目录。
- `~/.local/bin/wuditask` 指向工具 clone 的 `tools/wuditask.py`。
- 既有 skill 内容随 clone 更新直接生效；更推荐使用带候选验证的 `$wuditask-selfupdate` 或 `/wuditask-selfupdate`。
- 非 check 更新后若实际 skill 链接与 clone 不一致，selfupdate 返回 `reinstall_required=true`；此时无 `--replace` 地幂等运行一次 install，补齐新 symlink，并且只删除仍指向本 clone 的过期 skill symlink。普通内容更新不需要 install。
- 若一个长期运行的 agent 会话仍缓存旧 skill，重新打开会话即可，不需要 reinstall。
- 工具 clone 被移动或 Hub 远端改变时，重新运行 `$wuditask-install` 或 `/wuditask-install`。

查看 CLI 用法：

```text
wuditask help
wuditask help archive
wuditask help delete
wuditask help check
wuditask help assign
```

Agent 直接使用与操作同名的独立 skill：

| 操作 | Codex | Claude Code |
| --- | --- | --- |
| 添加任务 | `$wuditask-add` | `/wuditask-add` |
| 添加 GitHub 责任人 | `$wuditask-assign` | `/wuditask-assign` |
| 统一检查依赖和交付 | `$wuditask-check` | `/wuditask-check` |
| 启动 agent run | `$wuditask-execute` | `/wuditask-execute` |
| 归档结果 | `$wuditask-archive` | `/wuditask-archive` |
| 删除明确误建的归档记录 | `$wuditask-delete` | `/wuditask-delete` |
| 停止一个 agent run | `$wuditask-release` | `/wuditask-release` |
| 移除 GitHub 责任人 | `$wuditask-unassign` | `/wuditask-unassign` |
| 列出任务 | `$wuditask-list` | `/wuditask-list` |
| 查看单个任务 | `$wuditask-show` | `/wuditask-show` |
| 更新或维护 WudiTask | `$wuditask-selfupdate` | `/wuditask-selfupdate` |
| 安装或修复本机链接 | `$wuditask-install` | `/wuditask-install` |

安全检查并更新当前安装：

```text
Claude: /wuditask-selfupdate
Codex:  $wuditask-selfupdate
CLI:    wuditask selfupdate --check
        wuditask selfupdate
```

`--check` 只 fetch 工具远端并报告。实际更新要求工具 clone 干净，在临时
工具 clone 中运行完整测试，再执行 `merge --ff-only`。任务提交只推进 Hub
仓，不会触发 selfupdate。skill 链接漂移时返回
`reinstall_required=true`；配置无效或工具 remote/branch 与配置不符时直接
失败，必须重新运行 install。

在其他工作仓发现 WudiTask 自身需要修改时，使用
`/wuditask-selfupdate fix "问题描述"`（Codex 使用
`$wuditask-selfupdate fix ...`）。skill 基于配置的工具远端和分支创建隔离
worktree；它不读取或修改 Hub 任务来描述这次维护。工具仓默认仍用普通 push；
agent 自有维护分支经过 rebase/amend，或用户明确要求改写工具分支历史时，才可
对精确的 `tool_remote` 使用带已观察旧 OID 的 `--force-with-lease`。lease
失败必须停下检查新提交，不能刷新 lease 后自动覆盖。`hub_remote` 不存在这个
例外。SSH 与 HTTPS URL 必须先归一为 canonical 仓库身份，不能仅凭原始字符串
不同来判断工具仓与 Hub 不同。若配置的工具分支确实被改写，普通 selfupdate
仍在 divergence 上 fail closed；force-push 授权不会同时授权 reset 或重建现有
安装 clone。

## 日常命令

在任意工作仓库中添加任务。`repo` 永远表示执行仓；结构化 `source` 表示唯一
canonical Issue/PR。Issue/PR 必须先包含完整目标、上下文和 acceptance；Hub
JSON 不再复制这些内容。业务仓无法承载时，可以在配置的 Hub 建 fallback
Issue；不存在 text source。

省略 `--repo` 时 CLI 会读取当前仓库的 GitHub origin：

```bash
wuditask add \
  --source "https://github.com/acme/api/issues/42" \
  --priority P1
```

assignment 与 execution 是两个动作。默认给当前 login 指派，也可以在用户明确
授权时用 `--to` 指派另一个 login。无 ID execute 先选 assigned-to-self 的 idle
task，再选 unowned task；自动选择不会采用只由其他人拥有的工作。显式 task ID
表示用户选择加入该任务，execute 会在需要时把当前 login 添加为 co-owner。两种
需要新增 owner 的情况都先通过独立 GitHub 事务 self-assign，确认后再启动 Hub
run：

```bash
wuditask assign WDT-20260711T120000Z-A1B2C3
wuditask execute
```

`execute` 普通 push 确认后返回本次执行的 `run_id`。不同 owner login 可同时
执行；同一 login 最多一个 active entry。self-assignment 与 Hub start 不伪装成
原子事务；Hub start 失败时保留已确认的 assignment，并明确报告没有 run 启动。
统一检查一个任务或全部任务：

```bash
wuditask check WDT-20260711T120000Z-A1B2C3
wuditask check
```

停止执行只删除精确的 login/run pair，不改变 GitHub ownership；unassign 在目标
login 仍 active 时拒绝，要求先 release：

```bash
wuditask release WDT-20260711T120000Z-A1B2C3 \
  --run-id RUN_ID --reason "Waiting for a dependency"
wuditask unassign WDT-20260711T120000Z-A1B2C3
```

按 source 中的 acceptance 验证后归档。evidence 是自由文本列表，不再复制
`AC-N` schema：

```bash
wuditask archive WDT-20260711T120000Z-A1B2C3 \
  --run-id RUN_ID \
  --outcome done \
  --result "Validation added and regression tests pass" \
  --evidence "python3 -m unittest tests.test_upload: 12 tests passed"
```

`done` 始终要求当前 active agent 的 matching `--run-id`。failed/cancelled 若仍有
active agents，也要求调用者的 matching run 并在同一 archive commit 中清空全部
active entries；若已经没有 active agent，则只有 task `created_by` 可以在 source
明确终态后归档，并且必须省略 `--run-id`。旧 run ID 会被拒绝而不是忽略。这使
未认领或已经 release 的取消/失败任务不会卡死，同时保留明确的 authenticated
authority。

只有用户明确指出 archived record 本身是误建、重复或测试数据时，才使用独立
delete workflow。把所有 ID 放在同一批次；CLI 要求原因，并在任何目标不是
archive 或仍被批次外任务依赖时拒绝整批操作：

```bash
wuditask delete \
  WDT-20260711T120000Z-A1B2C3 \
  WDT-20260711T120001Z-D4E5F6 \
  --reason "These records were created by mistake"
```

delete 用同一个 Hub commit 移除 archive 记录，并在
`data/deletions/` 写入当前树中的持久回执。回执保存排序后的完整
ID 批次、reason、GitHub identity 和 UTC 时间；其中的任务 ID 永久保留，
不能重新创建。幂等重试只在确定性回执匹配且所有目标记录都已不存在
时成功；不同 actor 或 reason 不是同一操作。

delete 只允许配置的远端 Hub；`--local` 没有多文件 Git commit 的崩溃安全
边界，因此 CLI 会直接拒绝。

delete 不改 GitHub Issue/PR，也不清除 Git 历史、旧 clone 或已发布
Pages artifact，因此不是敏感信息擦除工具。

所有命令都支持全局 `--json`，skills 始终使用 JSON 输出。完整协议见 [docs/workflow.md](docs/workflow.md)。

## GitHub Pages

Pages workflow 位于 Hub 仓。它 checkout Hub，再 checkout workflow 中固定的
WudiTask 完整 commit SHA，使用工具仓的 validator 与 `site/` 构建
dashboard，并查询 canonical GitHub source 生成独立 delivery state。站点包含
任务列表、安装说明，以及可按全部仓或单个仓查看的任务依赖 DAG；全仓视图按
执行仓着色，节点以 canonical Issue/PR 编号为主标签。任务提交不会运行工具
测试。要发布 Pages，在 Hub 仓 Settings >
Pages 中把 Source 设为 **GitHub Actions**，再创建仓库变量
`WUDITASK_PAGES_ENABLED=true`。

```bash
gh variable set WUDITASK_PAGES_ENABLED --body true --repo OWNER/wuditask-hub
```

公开 source 可使用 workflow 的 `github.token`。跨仓私有 source 需要在 Hub
配置只读 secret `WUDITASK_GITHUB_TOKEN`，最小范围覆盖 repository metadata、
Issues、pull requests、checks 与 commit statuses；查询失败时 Pages 明确显示
`unavailable`，execute、check 与任何 archive 则 fail closed。

未设置该变量时，private 实践仓仍会完成校验和构建，但跳过 Pages 上传与部署，不产生错误的红色 workflow。

私有仓库是否能启用 Pages 取决于 GitHub 方案。个人 Pro、Team 或 Enterprise 通常可以从私有仓库发布 Pages；GitHub Free 的个人 private 仓不能启用。“源仓库私有”也不代表“站点私有”，一般站点仍然公开。只有具备相应 Enterprise Cloud 组织访问控制时，才应把 Pages 当作受限站点。snapshot schema v3 会包含 source title/body、owners、active-agent login、PR 作者/URL、review/check 摘要、归档证据和查询错误；`run_id` 不在 Pages 展示。初次演练必须使用脱敏任务。

## 为什么不是 todo.txt

`todo.txt` 很适合个人、线性、可读的待办事项；WudiTask 还需要跨仓依赖、
GitHub ownership、并发 agent runs、run-level ABA guard 与持久归档。canonical
narrative 和 acceptance 留在 Issue/PR，Hub 的最小协调 JSON 由
`schemas/task.schema.json` 与 `wuditask validate` 统一约束。

## 文档

- [安装与使用](site/install.md)
- [数据格式](docs/data-format.md)
- [分布式工作流](docs/workflow.md)
- [架构与并发模型](docs/architecture.md)
