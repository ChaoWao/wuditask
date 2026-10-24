# WudiTask

从 GitHub Issue / PR 直接生成业务 DAG，查看重点工作、负责人、交付和阻塞关系。

**[打开工作看板](https://chaowao.github.io/wuditask/)**

## 工作原则

- GitHub Issue / PR 是目标、进展、设计、验收和任务关系的唯一业务来源。
- Issue 显示负责人，PR 显示作者。关联 PR 随 Issue 展示，独立 PR 单独展示。
- 父子关系表示工作分解；只有明确的 blocking 关系表示前置依赖。
- 看板只读，不要求处理 Issue 前额外登记任务，也不替 GitHub 判断完成。
- 默认隐藏已关闭工作；同一 Issue 仍有关联开放 PR 时保留其上下文。

## 使用

只需 Python 3.10+ 和已登录的 GitHub CLI；无需安装 Python 依赖。

```bash
git clone git@github.com:ChaoWao/wuditask.git
cd wuditask
python3 tools/wuditask.py --json build-site \
  --github-scope github-view.json --output _site
python3 -m http.server 8000 --directory _site
```

在 `github-view.json` 配置重点根 Issue、仓库扫描范围及采集限制。
支持 DAG 展开/收起、节点摘要、人员与状态筛选、关联 PR，以及读取失败和过期提示。

## 发布与维护

Pages、范围配置和健康检查统一放在本仓库：

| 文件 | 用途 |
| --- | --- |
| `github-view.json` | GitHub 采集范围 |
| `.github/workflows/pages.yml` | 构建、定时刷新和发布 |
| `.github/workflows/pages-health.yml` | 线上数据新鲜度检查 |
| `site/github.*` | 业务看板界面 |
| `wuditask/github_graph.py` | GitHub 关系采集 |

部署方式、权限和数据边界见 [业务图说明](docs/github-business-graph.md)。

## 一个入口：`$wuditask`

对 agent 说 **“升级 WudiTask”**、**“处理这个 Issue”**、**“在这里接管继续”**、
**“暂停并保存进展”**。agent 负责 CLI、执行 ID 和交接记录。

已有两个仓库的安装直接升级，继续使用原仓库、原分支：

```bash
python3 TOOL/tools/wuditask.py --json upgrade
```

旧版还没有 upgrade 命令：

```bash
python3 TOOL/tools/wuditask.py --json selfupdate
python3 TOOL/tools/wuditask.py --json coord upgrade
```

[升级 skill](.agents/skills/wuditask/SKILL.md) 的 [升级流程](.agents/skills/wuditask/references/upgrade.md)
也可直接交给旧机器的 agent 执行。它更新工具、转换原执行分支、保留历史数据，
安装唯一 `$wuditask` skill 并清理旧链接。无需新建分支或手动停写证明。

新安装：`python3 tools/wuditask.py coord setup --remote EXECUTION_REMOTE --branch main`。
多人可共用执行库，记录按用户和任务独立。同账号各机器使用同一分支，默认防重复；
用户明确要求即可接管。旧进程不被强制终止，已发出的操作可能继续完成。

GitHub 是业务唯一台账；执行仓只存执行与恢复记录。详见 [个人协调协议](docs/personal-coordination.md)。
任务内规划、看板、agent 协作和交付由目标仓自己的 skill 负责。
在 simpler 中，领取后转交 [develop-task](https://github.com/hw-native-sys/simpler/blob/main/.claude/skills/develop-task/SKILL.md)。
WudiTask 继续提供执行权检查和进展记录，不规定内部任务格式或启动命令。

### Claude Code、Codex、OpenCode 共用

安装只维护一份 `wuditask` skill，通过软链接让客户端发现：

| 客户端 | 读取位置 | 显式调用 |
| --- | --- | --- |
| Codex | `~/.agents/skills/wuditask` | `$wuditask` |
| Claude Code | `~/.claude/skills/wuditask` | `/wuditask` |
| OpenCode | 兼容读取上述两个目录 | “使用 wuditask skill” |

三个入口指向同一份内容，升级工具后一起更新；其他机器仍需各自升级。
已运行的会话如缓存了技能目录，可开启新会话。能发现 skill 不代表每次都会调用；
需要个人协调时应明确要求使用它。客户端若禁用了技能或兼容目录，应按其当前配置检查。
依据：[Codex](https://developers.openai.com/codex/skills/)、[Claude Code](https://code.claude.com/docs/en/skills)、[OpenCode](https://opencode.ai/docs/skills/)。

旧版 `work` 命令已移除；已有本地计划、执行日志和产物保留，可由仓内流程读取接续。
升级不自动重启或终止 agent，也不改变现有 `coord` 执行记录。

## 开发验证

```bash
python3 -m unittest discover -s tests -v
```
