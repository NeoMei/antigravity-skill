# Antigravity Skill for Codex

从本机 Codex 聊天派单给 Antigravity Desktop、跟进实施和发起复审。Antigravity 的答复以 `Antigravity：` 加原文回到 Codex。

## 安装

需要在同一台 Mac 上运行 Codex 和已登录的 Antigravity Desktop，并使用 Python 3.9 或以上版本。

1. 启动 Antigravity Desktop 并登录。为 Codex 聊天使用的目录创建本机项目，只添加该目录。Git worktree 使用它自己的绝对路径。
2. 克隆仓库，从仓库根目录安装 Skill。已有同名 Skill 时，先备份原目录。
   ```sh
   mkdir -p "$HOME/.agents/skills"
   cp -R skills/antigravity "$HOME/.agents/skills/"
   python3 -B "$HOME/.agents/skills/antigravity/scripts/bridge.py" setup
   ```
3. 在 Codex 聊天里输入 `$antigravity 消息`。保持 Antigravity Desktop 运行。

初始化会为 Antigravity 添加一个本机完成回调。某个已登记的项目第一次收到请求时，桥接会为它启用 Sidecar。回调记录会话完成状态，桥接读取原生答复。任务状态和投递记录保存在本机 Antigravity 数据目录。

移动 Skill 后，在新位置重新运行 `setup`。它会更新此桥接的回调和 Sidecar 路径。尽量避免与 Antigravity 设置界面同时修改配置，若检测到配置变化，重试初始化或首次发送。

## 用法

| 输入 | 行为 |
| --- | --- |
| `$antigravity 消息` | 在当前主控会话继续问答 |
| `$antigravity 派单 需求` | 请主控给出实施事项和验收标准 |
| `$antigravity 复审 实施结果和证据` | 请同一主控会话复审 |
| `$antigravity 状态` | 查看当前任务状态和 ID |
| `$antigravity 新会话 消息` | 开一个独立会话和任务 |
| `$antigravity 恢复任务 消息` | 带着当前任务状态重开主控会话 |
| `$antigravity 恢复任务 任务ID 消息` | 在同一工作目录的另一个 Codex 聊天恢复任务 |

## 验证

`python3 -B -m unittest discover -s tests -v` 运行本机夹具测试，覆盖初始化、原生回传协议、同会话续接和跨项目隔离。夹具使用合成数据模拟 Antigravity Desktop。

[Codex 技能文档](https://learn.chatgpt.com/docs/build-skills) · [Antigravity Sidecar 文档](https://antigravity.google/docs/sidecars) · [Antigravity Hooks 文档](https://antigravity.google/docs/hooks)
