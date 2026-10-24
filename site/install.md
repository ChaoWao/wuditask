# 安装与使用 WudiTask

统一入口是 `$wuditask`（Claude Code 为 `/wuditask`）。对 agent 说“升级”“处理这个 Issue”“在这里接管继续”即可。

GitHub Issue/PR 保存业务事实，工具仓提供程序和看板，原执行仓保存个人执行与恢复记录。继续使用这两个仓库和原分支，不创建新的执行分支。

## 升级旧的两个仓库安装

把本页交给原机器上的 agent，它读取 `~/.wuditask/config.json` 找到工具和原执行仓。Python 3.10+、Git 和已登录的 GitHub CLI 是前提。

```bash
python3 TOOL/tools/wuditask.py --json upgrade
```

旧 CLI 尚无 upgrade 时：

```bash
python3 TOOL/tools/wuditask.py --json selfupdate
python3 TOOL/tools/wuditask.py --json coord upgrade
```

升级会验证工具测试并快进更新；在原执行分支转换数据，保留旧队列为 `data/legacy/`；只安装一个 skill，清理本安装管理的旧 skill 链接；配置各机器使用同一执行分支。旧活动记录不自动恢复，也不宣称已停止或完成。

工具 clone 有修改或分叉时不会丢弃；无关 skill 不覆盖。需要时先解决报告的冲突，再重试。若旧执行仓另有本地 clone，只在干净时快进；协调器以远端为准。

## 在新电脑上安装

使用原来的工具仓和执行仓（通常 main）：

```bash
git clone git@github.com:ChaoWao/wuditask.git
cd wuditask
python3 tools/wuditask.py --json coord setup \
  --remote git@github.com:TEAM/EXECUTION.git --branch main
```

远端分支须已存在。不同账号可共用执行仓；同账号各机器连同一分支。不要复制环境身份或 session 文件，不要把凭据和私密对话写入公共仓。

## 安装结果和验证

- `~/.wuditask/config.json` 保存两个仓库的位置，`personal/config.json` 保存本环境身份。
- `~/.agents/skills/wuditask` 和 `~/.claude/skills/wuditask` 指向统一 skill。
- `~/.local/bin/wuditask` 指向 CLI；若不在 PATH，可直接用 Python 调用。

```bash
wuditask --json coord inventory
wuditask help
```

## 日常使用

`$wuditask` 读取 Issue/PR 后按已有约定领取。默认拒绝本账号重复开工；你明确要求接管时无需停止证明，旧 run 在下次检查后停止新工作。系统不强制杀旧进程，已发出的外部操作仍可能完成。

领取后，主 agent 可通过同一入口建立本地 DAG 和 HTML 看板，派发 Codex/Claude 子 agent、验收结果并推进完整 PR。宿主没有可靠唤醒能力时，需手动继续原任务收取结果，不承诺后台自动续办。

暂停和完成只结束本次执行并保存摘要，不自动关闭 Issue。业务进展、设计和验收继续记录在 GitHub。

完整入口：[统一 skill 与升级流程](https://github.com/ChaoWao/wuditask/blob/main/.agents/skills/wuditask/SKILL.md)。工具源码：[WudiTask 工具仓](https://github.com/ChaoWao/wuditask)。
