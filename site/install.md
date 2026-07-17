# 安装与使用 WudiTask

WudiTask 是一个由 GitHub 和 Git 驱动的分布式任务队列。GitHub Issue 或 PR 记录任务描述、责任人和交付进展；独立的 Task Hub 保存执行租约、跨仓依赖和验收结果。工具仓与 Hub 必须是两个不同的 Git 仓库。

## 安装前准备

- Git。
- Python 3.10 或更高版本；WudiTask 不需要 pip 或 npm 安装。
- GitHub CLI `gh`，以及工具仓、Task Hub 和工作仓所需的访问权限。
- 一个长期保留的工具 clone 路径，以及独立 Task Hub 的 Git remote 和分支。

先登录 GitHub，并确认 CLI 能读取当前身份：

```bash
gh auth login
gh auth status
gh api user --jq '{login, id}'
```

WudiTask 用这个 GitHub 身份记录远端写入和 claim。认证失败时不要改用匿名或本地任务文件绕过检查。

## 在新电脑上安装

把工具 clone 放在稳定的数据目录，不要放在会被自动清理的临时目录。下面使用通用的 XDG 数据目录；请把 `OWNER` 替换为实际 GitHub owner：

```bash
WUDITASK_TOOL="${XDG_DATA_HOME:-$HOME/.local/share}/wuditask/tool"
mkdir -p "$(dirname "$WUDITASK_TOOL")"
git clone https://github.com/OWNER/wuditask.git "$WUDITASK_TOOL"
cd "$WUDITASK_TOOL"
```

运行工具 clone 自带的安装器，并显式传入另一个仓库作为 Task Hub：

```bash
python3 tools/wuditask.py --json install \
  --hub-remote https://github.com/OWNER/wuditask-hub.git \
  --hub-branch main
```

也可以让 agent 调用对应安装 skill：Codex 使用 `$wuditask-install`，Claude Code 使用 `/wuditask-install`。安装器会先拉取并校验 Hub；校验失败时不会写配置或链接。

如果 `~/.local/bin` 不在 `PATH`，把它加入 shell 配置后重新打开终端：

```bash
export PATH="$HOME/.local/bin:$PATH"
```

## 安装后有哪些内容

- `~/.wuditask/config.json`：记录工具 clone 的路径、remote 和 branch，以及独立 Hub 的 remote 和 branch。
- `${XDG_CACHE_HOME:-$HOME/.cache}/wuditask`：按 Hub remote 和 branch 分桶的持久 bare cache、临时 operation worktree 与本机锁。cache 可以重建，不是任务事实源。
- `~/.agents/skills/wuditask-*`：指向工具 clone 的 Codex skill 符号链接。
- `~/.claude/skills/wuditask-*`：指向同一工具 clone 的 Claude Code skill 符号链接。
- `~/.local/bin/wuditask`：指向工具 clone 中 Python 入口的符号链接。

配置与链接需要长期存在，Hub checkout 则只存在于 cache。不要把 Hub 当作工具仓的临时 clone，也不要让任务提交更新工具 clone。

## 验证安装

在任意目录运行：

```bash
wuditask --json validate
wuditask help
```

`validate` 必须成功报告 Hub schema、open、archive 和 deletion receipt 状态。写命令只有同时返回 `ok=true`、`confirmed=true` 和 `sync.confirmed=true` 才完成远端确认；`execute` 还必须返回 `work_authorized=true`，在此之前不要开工。

## 日常任务工作流

每个操作使用同名的独立 skill，避免用一个通用入口猜测意图：

1. 用 `$wuditask-add` 或 `/wuditask-add` 添加完整任务。
2. 用 `$wuditask-list`、`$wuditask-show` 或对应 Claude skill 查看任务。
3. 用 `$wuditask-dep-check` 检查跨仓依赖是否满足。
4. 用 `$wuditask-execute` 领取 ready 任务；确认远端 lease 后才开始修改执行仓。
5. 用 `$wuditask-reconcile` 对照 Hub coordination 与实时 GitHub delivery 状态。
6. 不再执行已领取任务时，用 `$wuditask-release` 释放 lease。
7. 验收完成后，用 `$wuditask-archive` 保存 done、failed 或 cancelled 结果和证据。

添加任务时，canonical source 按以下顺序选择：

1. 优先复用执行仓中匹配的 PR。
2. 其次复用或创建执行仓中的 Issue。
3. 只有执行仓无法承载描述时，才使用 Task Hub 的 fallback Issue。
4. 两个仓库都无法承载时，才使用带原因的 text source。

因此 GitHub 自动维护 assignee、关联 PR、review、checks 和关闭状态；WudiTask 只补充执行租约、依赖、验收条件与归档证据。不要手工编辑 Hub 中的 task JSON。

## 更新工具

先只检查远端更新：

```bash
wuditask selfupdate --check
```

确认后安全更新：

```bash
wuditask selfupdate
```

agent 应使用 `$wuditask-selfupdate` 或 `/wuditask-selfupdate`。更新会在候选 clone 中运行完整工具测试，再 fast-forward 当前工具 clone。若结果报告 `reinstall_required=true`，从工具 clone 重新运行 install，以补齐新的 skill link；普通 skill 内容更新会通过现有符号链接立即生效。

直接维护 WudiTask 本身时使用 `$wuditask-selfupdate fix <request>` 或 `/wuditask-selfupdate fix <request>`。它在隔离 worktree 中修改工具，不创建 GitHub Issue 或 WudiTask 队列项。

## 删除误建的归档记录

正常 done、failed 和 cancelled 任务必须保留在 archive。只有用户明确指出某些 archived record 是误建、重复或测试数据时，才调用 `$wuditask-delete` 或 `/wuditask-delete`：

```bash
wuditask delete \
  WDT-YYYYMMDDTHHMMSSZ-AAAAAA \
  WDT-YYYYMMDDTHHMMSSZ-BBBBBB \
  --reason "These archived records were created by mistake"
```

- 一次提交全部目标 ID；只要一个目标不是 archive，整批就会拒绝。
- 批次外任务仍依赖目标时拒绝删除，避免悬空依赖。
- 删除使用普通非 force Hub push，并写入包含 ID、原因、GitHub 身份和时间的持久回执。
- 已删除 ID 永久保留，不能重建；重试必须使用完全相同的 ID 批次、原因和身份。
- 删除不会修改 GitHub Issue 或 PR，也不会清除 Git 历史、旧 clone 或 Pages artifact，因此不是敏感信息擦除工具。

## 常见问题

### `gh` 未认证或权限不足

运行 `gh auth status` 和 `gh api user`，确认当前账号对工具仓、Hub 和 canonical Issue 或 PR 都有需要的权限。GitHub API 不可达时，claim 和 done archive 会 fail closed。

### 找不到 `wuditask` 命令

直接运行工具 clone 中的 `python3 tools/wuditask.py`，并确认 `~/.local/bin` 在 `PATH`。不要复制入口脚本；重新运行 install 修复符号链接。

### install 报目标路径已存在

先检查错误中的冲突路径。只有确认原内容应被保留为带时间戳的备份时，才显式重跑 `install --replace`；不要自动覆盖普通文件或其他工具的 skill。

### 工具 remote 与 Hub remote 相同

创建或选择一个独立 Task Hub。即使 SSH 和 HTTPS URL 看起来不同，指向同一个 GitHub owner/repository 也会被拒绝。

### Hub 校验或 schema 版本失败

先更新工具，并确认 Hub 的 `hub.json` 与工具支持的 API 完全匹配。WudiTask 不会静默迁移旧 Hub，也不会降级读取不兼容数据。

### selfupdate 拒绝更新

保持工具 clone 干净，并检查它是否位于配置记录的 remote 和 branch。分支发生 divergence 时 selfupdate 会 fail closed，不会 reset 本地历史。

### 长时间运行的 agent 看不到新 skill

先按安装结果重新运行 install，再重开 agent 会话。会话可能缓存旧 skill 指令，但不需要复制 skill 文件。

### cache 损坏或需要迁移

确认没有 WudiTask 命令正在运行后，可以删除 `${XDG_CACHE_HOME:-$HOME/.cache}/wuditask`。下一条命令会从配置的 Hub remote 重建 cache；`~/.wuditask/config.json` 和远端任务不会被删除。

### 写命令返回 `push_status_unknown`

不要猜测远端状态，也不要直接编辑 Hub。使用完全相同的参数重试同一命令，让 WudiTask 从最新远端快照做幂等确认或重新执行全部 guard。

更多背景可查看 [WudiTask 工具仓](https://github.com/ChaoWao/wuditask) 和当前站点的 [任务依赖图](dag.html)。
