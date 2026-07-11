# WudiTask

WudiTask 是一个以 GitHub 仓库为唯一事实源、供人类与 AI agent 共同使用的分布式任务队列。它没有常驻服务器、数据库或包管理器依赖；每个参与者只需要 Git、GitHub CLI 和 Python 3.10+。

## 系统边界

- **工具仓库**：保存 Python CLI、九个 agent skills、schema、dashboard 源码与测试。
- **Task Hub**：独立 Git 仓库，只保存严格版本的 `hub.json`、任务 JSON 与 Pages workflow。
- **工作仓库**：agent 实际修改代码的任意 GitHub 仓库。
- **人类身份**：通过 `gh api user` 取得 GitHub login 和不可变的 numeric ID。
- **任务所有权**：只记录人类 owner，不记录 agent owner；不同 agent 可以围绕同一人类身份沟通和交接。
- **并发协议**：只允许普通 push，绝不 force-push。写入失败后从远端新快照重试，并重新判断目标任务。
- **网页视图**：GitHub Actions 校验数据并生成只读 GitHub Pages；网页不直接修改任务。

## 目录

```text
wuditask tool repository/
  wuditask/                          纯 Python 核心
  tools/wuditask.py                  统一入口
  site/                              静态 dashboard
  .agents/skills/                    Codex/Claude 共用 skills
  schemas/task.schema.json           公开数据契约

wuditask-hub repository/
  hub.json                           严格 Hub/工具 API 契约
  data/open/<task-id>.json           未归档任务
  data/archive/<year>/<task-id>.json 永久归档
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
2. 校验固定的九项 WudiTask skill，并把它们链接到 `~/.agents/skills` 与 `~/.claude/skills`；缺少或多出 skill 都会拒绝安装。
3. 把一个无安装包的启动链接放到 `~/.local/bin/wuditask`。

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
wuditask help dep-check
```

Agent 直接使用与操作同名的独立 skill：

| 操作 | Codex | Claude Code |
| --- | --- | --- |
| 添加任务 | `$wuditask-add` | `/wuditask-add` |
| 领取并执行 | `$wuditask-execute` | `/wuditask-execute` |
| 检查依赖 | `$wuditask-dep-check` | `/wuditask-dep-check` |
| 归档结果 | `$wuditask-archive` | `/wuditask-archive` |
| 将已领取任务退回队列 | `$wuditask-release` | `/wuditask-release` |
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

在其他工作仓发现 WudiTask 自身需要修改时，使用 `/wuditask-selfupdate fix "问题描述"`（Codex 使用 `$wuditask-selfupdate fix ...`）。skill 基于配置的工具远端和分支创建隔离 worktree；它不读取或修改 Hub 任务来描述这次维护。

## 日常命令

在任意工作仓库中添加任务。若有明确的归属 GitHub 仓库，`$wuditask-add` 或 `/wuditask-add` 会优先复用匹配的 open Issue/PR；没有时在该仓创建 Issue，以其作为完整问题描述，并通过 `--link` 写入任务。WudiTask 的 goal/context/acceptance 只保留精简执行合同。没有合适仓库承载 Issue/PR 时才使用完整文本描述，但 schema v1 仍要求指定执行仓库。

省略 `--repo` 时 CLI 会读取当前仓库的 GitHub origin：

```bash
wuditask add \
  --title "Harden upload validation" \
  --goal "Reject malformed uploads before object storage" \
  --context "Preserve the current public API" \
  --accept "Malformed files return HTTP 400" \
  --verify "command::python3 -m unittest tests.test_upload" \
  --link "https://github.com/acme/api/issues/42" \
  --priority P1
```

领取当前工作仓库中优先级最高、无人领取且依赖已完成的任务：

```bash
wuditask execute
```

检查一个任务或全部未归档任务的依赖：

```bash
wuditask dep-check WDT-20260711T120000Z-A1B2C3
wuditask dep-check
```

验收后归档，而不是删除：

```bash
wuditask archive WDT-20260711T120000Z-A1B2C3 \
  --outcome done \
  --result "Validation added and regression tests pass" \
  --evidence "AC-1=python3 -m unittest tests.test_upload: 12 tests passed"
```

所有命令都支持全局 `--json`，skills 始终使用 JSON 输出。完整协议见 [docs/workflow.md](docs/workflow.md)。

## GitHub Pages

Pages workflow 位于 Hub 仓。它 checkout Hub，再 checkout workflow 中固定的
WudiTask 完整 commit SHA，使用工具仓的 validator 与 `site/` 构建
dashboard；任务提交不会运行工具测试。要发布 Pages，在 Hub 仓 Settings >
Pages 中把 Source 设为 **GitHub Actions**，再创建仓库变量
`WUDITASK_PAGES_ENABLED=true`。

```bash
gh variable set WUDITASK_PAGES_ENABLED --body true --repo OWNER/wuditask-hub
```

未设置该变量时，private 实践仓仍会完成校验和构建，但跳过 Pages 上传与部署，不产生错误的红色 workflow。

私有仓库是否能启用 Pages 取决于 GitHub 方案。个人 Pro、Team 或 Enterprise 通常可以从私有仓库发布 Pages；GitHub Free 的个人 private 仓不能启用。“源仓库私有”也不代表“站点私有”，一般站点仍然公开。只有具备相应 Enterprise Cloud 组织访问控制时，才应把 Pages 当作受限站点。初次演练建议使用脱敏任务。

## 为什么不是 todo.txt

`todo.txt` 很适合个人、线性、可读的待办事项，Tuxedo 也适合操作这类文本；但 WudiTask 需要跨仓依赖、结构化验收标准、GitHub numeric ID、claim token 与逐条证据。把这些压进标签会产生多套非标准解析规则，因此 canonical 数据采用一任务一 JSON。格式由 `schemas/task.schema.json` 和 `wuditask validate` 统一约束，而不是依赖某个额外 CLI。

## 文档

- [数据格式](docs/data-format.md)
- [分布式工作流](docs/workflow.md)
- [架构与并发模型](docs/architecture.md)
