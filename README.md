# WudiTask

WudiTask 是一个以 GitHub 仓库为唯一事实源、供人类与 AI agent 共同使用的分布式任务队列。它没有常驻服务器、数据库或包管理器依赖；每个参与者只需要 Git、GitHub CLI 和 Python 3.10+。

## 系统边界

- **任务仓库**：保存任务 JSON、Python 工具、agent skills 与 GitHub Pages 源码。
- **工作仓库**：agent 实际修改代码的任意 GitHub 仓库。
- **人类身份**：通过 `gh api user` 取得 GitHub login 和不可变的 numeric ID。
- **任务所有权**：只记录人类 owner，不记录 agent owner；不同 agent 可以围绕同一人类身份沟通和交接。
- **并发协议**：只允许普通 push，绝不 force-push。写入失败后从远端新快照重试，并重新判断目标任务。
- **网页视图**：GitHub Actions 校验数据并生成只读 GitHub Pages；网页不直接修改任务。

## 目录

```text
data/open/<task-id>.json             未归档任务
data/archive/<year>/<task-id>.json   永久归档
wuditask/                            纯 Python 核心
tools/wuditask.py                    统一入口
site/                                静态 dashboard
.agents/skills/                      Codex/Claude 共用 skills
.github/workflows/pages.yml          Pages 校验、构建、部署
schemas/task.schema.json             公开数据契约
```

## 快速开始

先克隆任务仓库并检查环境：

```bash
git clone git@github.com:YOUR-ORG/wuditask.git
cd wuditask
python3 tools/wuditask.py --local validate
```

然后在 Codex 中调用 `$wuditask-install`，或在 Claude Code 中调用 `/wuditask-install`。安装 skill 会：

1. 把当前 clone 的绝对路径写入 `~/.wuditask/config.json`。
2. 把 `wuditask` 和 `wuditask-install` 链接到 `~/.agents/skills` 与 `~/.claude/skills`。
3. 把一个无安装包的启动链接放到 `~/.local/bin/wuditask`。

也可以直接运行：

```bash
python3 tools/wuditask.py --json install
```

## 日常命令

在任意工作仓库中添加任务。省略 `--repo` 时会读取当前仓库的 GitHub origin：

```bash
wuditask add \
  --title "Harden upload validation" \
  --goal "Reject malformed uploads before object storage" \
  --context "Preserve the current public API" \
  --accept "Malformed files return HTTP 400" \
  --verify "command::python3 -m unittest tests.test_upload" \
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

每次任务变化都会执行 `validate`、测试和静态构建。要发布 Pages，在仓库 Settings > Pages 中把 Source 设为 **GitHub Actions**，再创建仓库变量 `WUDITASK_PAGES_ENABLED=true`。之后每次 push 自动部署，每小时还有一次安全刷新；页面每 60 秒读取一次最新 snapshot。

```bash
gh variable set WUDITASK_PAGES_ENABLED --body true --repo OWNER/wuditask
```

未设置该变量时，private 实践仓仍会完成校验和构建，但跳过 Pages 上传与部署，不产生错误的红色 workflow。

私有仓库是否能启用 Pages 取决于 GitHub 方案。个人 Pro、Team 或 Enterprise 通常可以从私有仓库发布 Pages；GitHub Free 的个人 private 仓不能启用。“源仓库私有”也不代表“站点私有”，一般站点仍然公开。只有具备相应 Enterprise Cloud 组织访问控制时，才应把 Pages 当作受限站点。初次演练建议使用脱敏任务。

## 为什么不是 todo.txt

`todo.txt` 很适合个人、线性、可读的待办事项，Tuxedo 也适合操作这类文本；但 WudiTask 需要跨仓依赖、结构化验收标准、GitHub numeric ID、claim token 与逐条证据。把这些压进标签会产生多套非标准解析规则，因此 canonical 数据采用一任务一 JSON。格式由 `schemas/task.schema.json` 和 `wuditask validate` 统一约束，而不是依赖某个额外 CLI。

## 文档

- [数据格式](docs/data-format.md)
- [分布式工作流](docs/workflow.md)
- [架构与并发模型](docs/architecture.md)
