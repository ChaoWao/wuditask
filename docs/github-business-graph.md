# GitHub 业务图

`build-site --github-scope` 直接从 GitHub 总 Issue 与配置的仓库读取业务图，不需要安装配置、
Hub task、`add` 或 `archive`。GitHub 的关闭原因与原生关系决定图中的前置状态。
该模式不修改 GitHub，也不读取旧 Hub 执行记录。

## 本地生成

需要 Python 3.10+、已认证的 `gh` 和对所选公开仓库的读取权限：

```bash
python3 tools/wuditask.py --json build-site \
  --github-scope examples/github-scope.json --output _site
python3 -m http.server 8000 --directory _site
```

在浏览器打开 `http://localhost:8000`。必须通过 HTTP 查看交互图；`file://` 下浏览器
通常不允许脚本 fetch JSON。`graph.json` 可单独下载阅读。

配置文件只声明展示范围，不是逐任务登记：

```json
{
  "roots": ["https://github.com/hw-native-sys/simpler/issues/2670"],
  "repositories": ["hw-native-sys/simpler"],
  "max_nodes": 500,
  "max_pages": 20,
  "stale_after_seconds": 10800
}
```

支持 github.com Issue/PR URL 根节点。`roots` 决定重点工作，`repositories` 可选，
分页采集指定公开仓库的所有 open Issue/PR；不要求每个任务先挂到根 Issue。
继续递归读取原生子 Issue、blocked-by 前置与正式交付关系。PR 的反向关闭关系
也会读取，因此一个开放 PR 关联已关闭 Issue 时仍能归组。
多个根和仓库允许跨仓展示；Project、Enterprise host 和私有业务图尚未支持。

单独一行的 `Part of #123.` 或 `Part of https://github.com/owner/repo/issues/123`
可提供明确的正文关联；代码、引用、HTML 注释与折叠区不算声明。
正文关联使用虚线及来源 PR 链接，与正式关闭关联分开；不推导阻塞或完成。
其他普通编号提及和链接不自动连边。正式关系覆盖同一对对象的正文关联。

`max_nodes`、`max_pages` 是采集上限；达到上限显示 partial/unknown，不能伪装完整。
每次调用重新读取，包括 closed 对象；不靠旧 Hub archive 判断完成。
API 分页完整读取，网络/权限/GraphQL 部分错误按字段暴露，错误消息不原样发布。
每个对象采集后重读更新时间和 PR head，发生变化时标记不稳定；这不是全局事务快照。
来源稳定 node ID 去重；URL 重定向后核实当前身份。跨转移发生 node ID 变化时不猜测
两个对象相同。页面只读，不从身份推导执行领取权。

## 视图与状态

- **重点工作**：沿用 SVG DAG 图，配置根 Issue 的子任务与关联 PR 以节点连线展示；
  支持缩放、适应宽度及逐节点展开/收起。正式关联使用实线，正文关联使用虚线。
- **其他工作**：未归入重点工作的 Issue 以及无关联 PR，按 GitHub assignee 分组。
  分组仅用于阅读，不创建 GitHub 层级，不从 PR 作者推断负责人。
- 图与列表都以 Issue 为主项，关联 PR 作为子项。一个 PR 可关联多个 Issue，
  每个目标下可见，关联 PR 数量按身份去重；PR 不在另一个负责人分组重复作为独立工作。
- 负责人筛选以 Issue 为单位，保留全部关联 PR；搜索 PR 时显示所属 Issue。
  搜索子 Issue 保留可用的祖先上下文。列表默认展开，不受树的折叠影响。
- 默认隐藏 closed / merged。若已关闭 Issue 仍有关联的开放 PR，则保留该 Issue
  作为归属上下文并显示 Closed；不会把 PR 误报成独立工作。已关闭父项的开放子项
  仍可见。选择“全部状态”可查看已采集的历史，仓库扫描本身只收集开放对象。
- 阻塞状态在对应节点提示，前置身份和关系可从右侧来源详情查看，不设空的阻塞视图。
  只用原生阻塞边判断前置状态；子 Issue 关系及 PR 关联不产生执行依赖。
- 悬停或键盘聚焦显示摘要；Escape 关闭。关系环及未知边界不因折叠而被当作有效 DAG。
- 点击节点后，右侧优先显示正文中的目标、当前进展、剩余工作/阻塞与下一步。
  优先提取“当前概况”字段，也识别 Goal、已交付基线、当前阻塞与解除条件、
  近期交付顺序、Summary 等中英文标题；没有目标段时取正文开头。
  这是作者原文的有界摘录，不是 AI 生成结论。缺项不推断；每段最多三条、每条
  420 字符，截断明确提示。历史折叠区、代码块、HTML 注释不作摘要来源。
  状态字段、时间戳、PR 检查和关系列表收进默认关闭的“来源详情与关联任务”。
- 默认“未完成”支持折叠；筛选时展开命中路径，清除后恢复折叠状态。
- 支持仓库、负责人、状态、标题筛选，单仓隐藏仓库筛选。
- Issue 的唯一 assignee 是可解析负责人；零或多个分别显示 unassigned/conflict，
  不从 PR 作者静默推断 Issue owner。PR 同样展示 assignees，不自动改派。
- PR 详情包含观察到的 head SHA、审查结论和该提交的 check/status 统计；不把
  “所有 checks 成功”当作仓库分支保护全部满足，也不宣称自动可合入。

| 状态 | 含义 |
| --- | --- |
| satisfied | 已记录的前置均 closed/completed，相关观察无错误；不是开工授权 |
| unrecorded | 没读到前置关系；不声称没有遗漏的前置 |
| blocked | 有前置 Issue 仍 open |
| needs_assessment | 前置取消或关闭原因不明确，需在 GitHub 核定 |
| unknown | 读取失败、受限、截断或对象在采集中变化 |
| invalid | 存在依赖环或受环影响 |

PR merged + Issue open 仍显示目标未完成。某次 agent 成功/失败、旧 Hub archive
均不改变业务图的判断。当前执行与内部计划尚未接入，页面不判断谁正在执行。

## 展示范围

采集全仓不等于把所有节点平铺到同一张图：主线树与其他工作分开浏览，
关联 PR 始终随 Issue 归组。工作事项与关联 PR 分开计数，不把交付代码当成第二个任务。

页面保留筛选、工作图和正文摘要。列表默认展开，零项诊断隐藏；过期、读取失败、
部分采集和依赖环仍明确提示。正常同步时间和操作教学提示不展示。

## 在 WudiTask Pages 部署

正式入口：[WudiTask](https://chaowao.github.io/wuditask/)。范围配置和发布、健康检查
workflow 与工具代码放在同一仓库。`github-view.json` 选择 GitHub 根节点、全仓采集范围和采集限制。
旧 `wuditask-hub` Pages 保留跳转；业务采集不依赖 Hub 仓库或执行记录。

```bash
python3 tools/wuditask.py --json build-site \
  --github-scope github-view.json --output "$GITHUB_WORKSPACE/_site"
```

在工具仓 Settings → Pages 选择 GitHub Actions，并设置仓库变量
`WUDITASK_PAGES_ENABLED=true`。每次构建 checkout 已提交的默认分支并运行完整测试，
不执行 PR 分支代码。读取使用 workflow token，或可选的 `WUDITASK_GITHUB_TOKEN` secret。
发布按并发组串行执行：相关代码/配置 push、手动触发，以及每小时第 17 分钟。
业务仓 Issue 事件不会直接触发本仓 workflow，定时采集负责补齐。

独立健康检查在每小时第 47 分钟运行，也支持手动触发；检查线上快照完整性和新鲜度。
维护者需启用 Actions 失败通知；GitHub 调度中断不保证能自行报警。
旧站不再定时采集，避免存在两份独立刷新、状态不一致的看板。

所有根都不可读时命令失败，不替换本地上一份输出；Actions 应停止发布，现有 Pages
仍保留上次部署，浏览器会根据数据年龄标旧。有可读根但部分字段失败时发布明确的
partial 图并返回 `status=partial`。workflow 应在 job summary 给出诊断；不得把
构建进程成功等同采集完整。页面刷新只读取最新已部署快照，不在浏览器使用 GitHub token。

本地输出使用暂存目录，完成后切换；要求单发布者。输出目录须带本模式标记且只含
本站产物，拒绝覆盖非本站目录或旧 dashboard。CI artifact 使用独立目录再由 Pages
整体部署，因此不会把旧业务数据混入新生成物。

公开 Pages 只读取已核实为 public 的仓。私有或无法核实可见性的引用显示匿名边界，
不发布其 URL、标题、owner 或正文。公开节点新增上述正文摘要；不镜像完整 body 或评论，不读取 token、机器配置
或旧执行回执作为内容。公开正文摘录会发布到公开 Pages，维护者应按公开资料维护源 Issue。源仓私有不等于 Pages 私有；私人执行详情必须采用独立受控存储/视图。

## 迁移边界

旧的 `build-site`（未传 scope）和 execute/archive 等命令暂时维持原协议，供现存
执行台账继续使用，不能当作新的个人协调模型。新业务图不调用它们，也不把它们
嵌入公开页面。后续迁移执行状态前，需要盘点旧数据、确认活动 run 收尾，并确保
同用户全部环境切到单一权威；本次不删除或改写旧记录。

## 验证

```bash
python3 -m unittest tests.test_github_graph -v
python3 -m unittest discover -s tests -v
node --check site/github.js
```

覆盖跨仓关系、分页/截断、失败读取、取消与重开、依赖环及下游、身份重定向、
public/private 边界、更新中的 PR、无 Hub 配置构建、旧产物保全和前端模型。
真实采集结果和浏览器检查须另外记录，单元测试不替代生产部署验收。

API 依据：[子 Issue](https://docs.github.com/en/rest/issues/sub-issues)、
[Issue dependencies](https://docs.github.com/en/rest/issues/issue-dependencies)。
