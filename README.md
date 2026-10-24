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

## 个人跨环境协调（Part 2）

用户使用 **`$wuditask-coordinate`**，直接说“处理这个 Issue”“在这里继续”“暂停”。
主 agent 负责配置、领取、核对和交接，无需用户管理 run ID 或填写回执。

```bash
python3 tools/wuditask.py coord setup \
  --remote git@github.com:TEAM/EXECUTION.git --branch personal-execution
```

多人可共用一个执行库，记录按用户和任务独立。setup 使用新分支，不要求清理或
迁移历史。默认避免本账号重复开工；用户明确要求即可接管，不要求旧进程停止证明。
旧 agent 下次检查时应停新工作，但系统不强制阻止已发出的写入。

详细交互及内部 CLI 见 [个人协调协议](docs/personal-coordination.md)。严格停写
核验仍是可选方式；[存量队列协议](docs/legacy-execution.md) 只用于旧任务维护。
**Part 3** 的内部 DAG、子 agent 调度和自动停止仍独立推进。

## 开发验证

```bash
python3 -m unittest discover -s tests -v
```
