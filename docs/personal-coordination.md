# 个人跨环境执行协调

GitHub Issue/PR 保存业务目标、责任、进展和验收。`coord` 只保存同一 GitHub
账号在多个环境里的执行权，不建另一份业务队列，不自动指派、不关闭 Issue，
也不要求归档后才能开始下游工作。业务看板无需接入本协议。

## 保证与边界

- 同一权威 Git 远端/分支内，以 **GitHub 用户稳定 ID + 任务 node ID** 互斥。
  URL 别名、仓库改名和用户改名不产生第二把锁。
- 同用户不同任务可并行；不同用户互不释放、不要求共享一把执行锁。
- 普通 Git push 冲突后重读重算；不 force-push。响应不确定时保留本地操作
  journal，重试同一 session，确认前不启动。历史提交已接受仍须再核对当前 claim。
- 执行状态只有 `active → quiescing → released`。无 TTL 抢占；断线不等于退出。
- 一个账号在所有环境必须使用同一权威远端和分支。配置一次，不按机器新建权威库。
  多账号归并、跨任务文件冲突、共享硬件调度不在保证范围。
- 客户端与 Git 写凭据属于用户信任域。本地环境标识不是独立安全认证；普通
  GitHub 写入和任意文件写入没有服务端 fencing，不能防止协议外或恶意进程。

## 配置和迁移

需要 Python 3.10+、Git 和已登录 github.com 的 `gh`。工具可以直接从 clone 运行，
不需要安装 Python 依赖。执行存储是一个**已有至少一次提交**的独立 Git 远端。
包含私人机器信息或交接材料时必须使用私有仓库；公开仓的分支不提供隐私隔离。
凭据留在 SSH agent / Git credential helper 中，不放进 remote URL 或 JSON。

```bash
python3 tools/wuditask.py coord configure --remote git@github.com:USER/PRIVATE-EXECUTION.git
python3 tools/wuditask.py --json coord inventory
python3 tools/wuditask.py coord init --old-writers-stopped '新建空存储，没有旧执行器'
```

每个环境独立执行 `configure`，但使用同一 remote/branch。配置保存在
`~/.wuditask/personal/config.json`，含独立 environment ID；不要跨机器复制配置或
session journal。首版只允许一个账号、一份权威配置，拒绝静默切换 authority。

迁移旧 Hub 时：

1. 停止该远端的**所有**旧 writer（包括定时任务）；只读 `inventory` 查看重复
   source、活动 run 和历史记录。
2. 核实旧执行及子 agent 已停写，再用旧协议释放活动 run。未核实的执行保持占用。
3. `init --old-writers-stopped '停止核验/交接记录的引用'` 原子更新协议标记，
   保存旧记录哈希与迁移回执。存在任何活动旧 run 则拒绝迁移。
4. 旧记录原样保留，只读审计；新任务按 GitHub 身份直接领取，不需要转换 WDT ID。
   新 manifest 使旧客户端拒绝写入；迁移前已准备的旧提交会被普通 push 冲突拒绝。

这是**整个存储的原子切换**，不支持同一远端里部分用户继续写旧协议。GitHub
Issue/PR、负责人、DAG 不因迁移改变。工具升级不会自动迁移任何实际远端。

## 领取与执行

```bash
python3 tools/wuditask.py --json coord acquire \
  https://github.com/ORG/REPO/issues/123 --session ./execution.json
python3 tools/wuditask.py --json coord check --session ./execution.json
python3 tools/wuditask.py coord guard --session ./execution.json -- HOST_COMMAND ARGUMENTS
```

- `acquire` 在远端写入前持久化 run/session 身份。命令响应丢失时原样重试，
  不换 session 文件，也不创建另一个 run。返回成功不等于已经启动 agent。
- `guard` 再核对远端 claim，持有本地 session 锁执行一个宿主命令。每份 journal
  只尝试启动一次。不同本地进程不能同时使用该 session；复制 journal 到另一
  路径/环境不能恢复它。启动失败或宿主中断保持占用，须核验退出后交接。
- `guard` 返回子命令的 `returncode`（子命令失败时 CLI 也失败），不会自动释放；子进程退出不证明远端子 agent
  或后台写入已停止。宿主命令应在前台运行，不能用 daemonize 作为交接信号。
- `check` 是 Part 3 受控写入前的核对入口；它不是对后续 GitHub 写入的原子锁。
- 已有关联 closing Issue 的 PR 必须提供 `--anchor ISSUE_URL`，或直接领取约定的
  Issue。独立 PR 可直接领取。同一 PR 关联多个 Issue 时由主 agent 明确范围，
  不自动把全部 Issue 合并成一把锁。普通 mention 不是 closing 关系；语义重复
  和不同任务共改文件仍须主 agent 识别。

`coord inspect URL` 返回当前占用和历史 run，包含 checkpoint。它不会因 Issue
关闭、PR 合入或责任变更自动清除执行权。

## 停止、交接和恢复

```bash
python3 tools/wuditask.py coord quiesce --session ./execution.json
# 主 agent 停止派发；核验主会话、子 agent、后台进程、未决 push/API 等已停止或对账。
# 保存可被接班环境访问的 checkpoint，填写下面的停止核验文件。
python3 tools/wuditask.py coord release --session ./execution.json --receipt ./stop.json
```

首版只接受 **manual-verification**。`stop.json` 的身份字段从 acquire/inspect 的
run 复制，操作 ID 一次生成后固定；相同操作重试必须使用相同内容：

```json
{
  "operation_id": "stop-unique-id",
  "actor_id": "12345",
  "task_id": "I_github_node_id",
  "run_id": "WDX_用实际输出替换",
  "environment": "旧环境实际值",
  "session": "旧会话实际值",
  "method": "manual-verification",
  "verified_by": "核验者",
  "writers_stopped": true,
  "pending_operations_reconciled": true,
  "evidence": "具体停止核验与外部操作对账记录",
  "checkpoint": "接班环境可读取的交接材料引用"
}
```

布尔值本身不是进程停止证明：使用者必须实际核验并记录依据。工具检查字段、
exact run/环境身份、quiescing 状态和幂等回执；不宣称能独立核验人工陈述。
停止/隔离依据不足时不填回执、不释放。正常退出不需要重复取得用户审批。

新环境先 inspect，读取 checkpoint、检查代码基线和未完成操作，再用自己的
配置和**新 session 文件** acquire。已释放 journal 不能重新启动旧 run。

旧机器失联时，由用户明确授权恢复，并先核实/隔离旧写入。为旧 run 填写上述
回执，另加 `user_authorization` 记录授权依据，然后：

```bash
python3 tools/wuditask.py coord recover ISSUE_URL --run-id OLD_RUN_ID --receipt recovery.json
```

恢复不要求取得旧机器的 journal，但必须匹配旧执行身份。它只释放已核验的旧
run，不自动启动接班任务。无授权或不能排除旧 writer 继续写入时保持占用。

## 实现和 Part 3 接口

- `execution_cli.py`：GitHub 身份、authority 配置、本地 journal 和会话锁、命令入口。
- `coordination.py`：状态机、精确释放、历史回执、迁移检查。
- `gitops.py`：复用的 bare cache、隔离 worktree、普通 push 和冲突重放。
- 远端 `coordination.json` 是执行协议标记；`data/execution/claims/<hash>.json`
  保存 actor/task 原始身份、当前 run 和历史；`data/execution/migration.json`
  保存迁移回执。历史暂不裁剪，以保留旧操作的幂等判断。

Part 3 负责目标对齐、内部 DAG、子 agent 派发/停止、checkpoint 内容和验收；
调用本协议前核对业务授权。首版提供受锁保护的宿主启动与人工核验交接，
**不包含自动停止整棵进程树、远端子 agent 纳管或自动恢复模型会话**。

测试使用临时真实 Git 远端验证竞争、响应丢失、迁移和恢复；GitHub 身份和任务
查询使用受控响应。它们不代表线上网络故障演练或具体模型宿主的自动交接验收。
