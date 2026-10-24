# 个人跨环境执行协调

**用户通过 `$wuditask` 对主 agent 说要做什么，CLI 由 agent 调用。**
GitHub Issue/PR 保存目标、责任、设计、进展和验收；这里记录当前由哪个环境推进。
不再人工创建 WDT 任务，不自动指派，不用 Hub archive 判定业务完成。

## 用户交互

| 用户意图 | agent 的动作 |
| --- | --- |
| 处理这个 Issue | 读取任务、对齐目标和方案、领取执行 |
| 谁在做／进展如何 | inspect，汇总当前执行及 checkpoint |
| 在这里继续／接管 | 读取旧进展，明确替换刚观察到的旧 run |
| 暂停 | 保存交接摘要，finish paused |
| 完成 | 验证交付，按已有授权更新 Issue/PR，finish completed |

用户不需要填写 run ID、session 路径或停止回执。重复网络请求沿用 agent 保存的
同一 session journal；不能因响应不确定而盲目启动第二个执行。

## 一个共享库即可

多人可以共用一个执行仓库和分支。记录按 **GitHub 用户稳定 ID × 任务 node ID**
隔离：同账号两台机器默认防重复；不同账号处理同一 Issue 互不阻止。Issue 的
业务负责人不因领取而改变。一个账号的所有环境必须使用相同的权威 remote/branch。

每人使用私有执行库也支持，但不是必要条件。共享库只存必要的执行状态和进展
摘要；敏感对话、主机细节和私人恢复材料不要写入公共库。凭据放在 SSH agent 或
Git credential helper，不写 remote URL 或 JSON。

## 统一升级与安装

只保留 `$wuditask` 一个 skill。说“升级”执行升级流程，说“处理这个 Issue”执行
任务流程；领取、接管和结束不再各装一个 skill。需要 Python 3.10+、Git 和已登录的 gh。

已经安装旧工具仓和旧执行仓的机器：

```bash
python3 TOOL/tools/wuditask.py --json upgrade
```

旧 CLI 没有 upgrade 命令时，先运行旧 `selfupdate`，再调用更新后的 `coord upgrade`。
[升级 skill 流程](../.agents/skills/wuditask/references/upgrade.md) 包含缺少配置时的旧 clone 识别方法。

升级复用原两个仓库和原执行分支（通常 main），不会新建 personal-execution 分支：

1. 工具只做干净工作区的快进更新，先验证候选测试；本地修改/分叉不覆盖。
2. 执行仓在一次普通 Git 提交中转成新格式。旧 open/archive/deletions 和 manifest
   移入 `data/legacy/`，保留原始内容；原活动记录只是历史，不宣称完成或自动恢复。
3. 旧客户端下次访问新 manifest 会拒绝继续写；不要求证明旧进程停止。
4. 已经是新格式的执行仓保持现有 claims 不变，重复升级为幂等操作。
5. 注册单一 skill、移除本安装管理的旧 skill 链接，并配置本机使用同一个执行分支。
   不删除无关文件；中途失败明确报错，可修复后重试。

新安装从工具 clone 执行 `coord setup --remote EXECUTION_REMOTE --branch main`。
使用既有分支；不会另建仓库或分支。各机器分别升级，不复制 environment/session。
如果旧执行仓 clone 已知且干净，也快进同步它；工作区有修改则保留，协调器直接访问远端。
长期运行会话若缓存技能目录，需刷新或重开。

临时绑定 personal-execution 的安装可通过 upgrade 回到原 main，但源分支存在
执行历史时拒绝静默改绑，必须先处理历史转移。普通升级不会自动删除远端分支。

## 领取、核对、保存进展

以下是 skill 的内部接口，用户不必手工操作：

```bash
wuditask --json coord acquire ISSUE_URL --session SESSION_FILE
wuditask --json coord check --session SESSION_FILE
wuditask coord checkpoint --session SESSION_FILE --summary '已完成什么；下一步；产物引用'
```

acquire 先持久化本地 run/session，再尝试提交领取；默认发现同账号同任务已有执行
就报冲突。Git push 非快进后重读并重新判断，不覆盖其他执行。响应丢失时重试
同一命令/session；提交出现在历史中仍须后读当前 claim 才可继续。

从 Issue 开始的执行一直以 Issue 协调，PR 是交付产物。独立 PR 可以自身作为任务；
PR 存在 closing Issues 时通过 `--anchor ISSUE_URL` 明确范围。不自动把多个 Issue
合成一把锁，普通 mention 也不自动建立锁关系。

主 agent 在启动工作、接收新的工作指令和受控外部写入前 check。已被替换或结束
就停止新派发/写入，尝试停止自己的子 agent；不能自动抢回。当前会话可直接使用
acquire/check，无需再启动一个模型。可选的 `coord guard --session FILE -- COMMAND`
提供宿主单次启动和本地会话锁，不监控或杀掉旧进程，也不自动释放记录。

## 明确接管，不要求证明旧进程停止

```bash
wuditask --json coord inspect ISSUE_URL
wuditask --json coord takeover ISSUE_URL \
  --expected-run-id OBSERVED_OLD_RUN --reason '用户要求在这里继续' --session NEW_SESSION
```

takeover 在一次 Git 提交中把**观察到的那个旧 run**标为 superseded，并安装新 run。
保存用户指令、旧新关系及已有 checkpoint；新 run 使用新 journal。

- 不要求旧环境在线，不要求停止证明，不因旧进程仍存在而拒绝接管。
- 如果当前 run 已变化，则返回 takeover_conflict。不能不断换目标循环抢占。
- 响应丢失时沿原 journal/目标/理由重试；旧接管请求不能复活已经被替换的自己。
- 旧 agent 下一次 check 会发现失去当前执行权。已发出的写入和协议外进程可能
  继续运行；这是**当前主执行的协调**，不是强制进程互斥或文件写入 fencing。
- 正常领取不自动接管；明确用户指令已经足够，不需要再问一次批准。

## 暂停和结束不需要 JSON 回执

```bash
wuditask coord finish --session SESSION_FILE --outcome paused --summary '接班需要知道的内容'
# outcome 也可为 completed / cancelled
```

finish 保存 checkpoint 并结束自己的记录，不替换后继 run，不修改 Issue/PR，
也不声称所有子进程已退出。agent 应停止自己的新工作，已知仍在运行的后台工作
写入摘要。重试同一 finish 必须保持 outcome/summary 一致。

执行权状态为 active → released；quiescing 只服务可选的严格交接。released 仅表示
记录不再是当前执行，是否经过停止核验由 stop_verified 单独表示。

## 可选：严格交接与旧数据迁移

需要核验停写的场景仍支持 `quiesce`、`release --receipt`、`recover --receipt`。
这不是普通接管的门槛。receipt 匹配 actor/task/run/environment/session，并包含
operation_id、method=manual-verification、verified_by、evidence、checkpoint、
writers_stopped=true、pending_operations_reconciled=true；recover 另需 user_authorization。
它记录人工核验，不独立证明进程死亡。普通用户不需要填它。

旧的严格 `init --old-writers-stopped` 接口保留给显式要求停止核验的存储迁移；
默认升级用 `upgrade`，无需停止证明。

## Part 3 接口和验证范围

Part 3 负责目标对齐、内部 DAG、子 agent 管理、实际停止、checkpoint 内容和交付。
S2 管理执行身份及条件更新。不同账号互不锁定；同账号不同任务写同一文件、硬件
排队、任意副作用 exactly-once 和强制取消进程不在这里处理。

测试使用临时真实 Git 远端覆盖领取/接管竞争、丢响应、旧 run 失效、不同用户独立、
结束幂等、原分支升级、历史保留及旧安装技能清理；GitHub 解析使用受控响应测试。这不等于所有 agent 宿主
已经实现主动撤销或自动停写。所有环境使用新 skill 后，才属于新流程的协调范围。
