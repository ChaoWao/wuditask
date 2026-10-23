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

同一个 GitHub 账号在多台机器处理同一任务时，由 `coord` 维护一个获准主执行。
直接使用 Issue/PR URL，无需人工 add，不修改 GitHub 指派或业务完成状态。

```bash
python3 tools/wuditask.py coord configure --remote git@github.com:USER/PRIVATE-EXECUTION.git
python3 tools/wuditask.py coord inventory
python3 tools/wuditask.py coord init --old-writers-stopped '新建空存储，没有旧执行器'
python3 tools/wuditask.py coord acquire https://github.com/ORG/REPO/issues/123 --session execution.json
```

支持持久 run 重试、当前执行核对、宿主启动锁、停写、checkpoint 交接和显式恢复。
首版交接要求人工核验旧 writer 与外部操作已停止；无自动超时抢占，也不声称能强制
停止任意进程。详细使用和迁移见 [个人协调协议](docs/personal-coordination.md)。

旧数据不会随工具升级自动迁移；[存量协议](docs/legacy-execution.md) 只用于迁移前维护。
业务看板无需执行存储。**Part 3** 的目标对齐、内部 DAG、子 agent 调度和宿主自动
停写仍独立推进，通过 Part 2 接口领取和交接，不另建业务台账。

## 开发验证

```bash
python3 -m unittest discover -s tests -v
```
