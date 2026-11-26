# Antigravity Skill for Codex

从本机 Codex 聊天向 Antigravity Desktop 提问、派单和发起复审。Gemini 可把工作分给多个独立的 Codex 会话，再在原主控会话检查结果。Antigravity 的答复以 `Antigravity：` 加原文回到 Codex。

## 安装

需要在同一台 Mac 上运行 Codex 和已登录的 Antigravity Desktop，并使用 Python 3.9 或以上版本。

1. 启动 Antigravity Desktop 并登录。
2. 克隆仓库，从仓库根目录安装 Skill。已有同名 Skill 时，先备份原目录。
   ```sh
   git clone https://github.com/NeoMei/antigravity-skill.git
   cd antigravity-skill
   mkdir -p "$HOME/.agents/skills"
   cp -R skills/antigravity "$HOME/.agents/skills/"
   python3 -B "$HOME/.agents/skills/antigravity/scripts/bridge.py" setup
   ```
3. 在目标目录的 Codex 聊天里输入 `$antigravity 消息`。保持 Antigravity Desktop 运行。首次发送时，桥接会按实际绝对路径自动登记本机项目，Git worktree 使用自己的路径。

初始化会为 Antigravity 添加一个本机完成回调。某个工作目录第一次收到请求时，桥接会为它启用 Sidecar。回调记录会话完成状态，桥接读取原生答复。任务状态和投递记录保存在本机 Antigravity 数据目录。

移动 Skill 后，在新位置重新运行 `setup`。它会更新此桥接的回调和 Sidecar 路径。尽量避免与 Antigravity 设置界面同时修改配置，若检测到配置变化，重试初始化或首次发送。

### 专用聊天

如果想在同一个 Codex 聊天里直接和 Antigravity 对话，先登记一次会话模式入口：

```sh
python3 -B "$HOME/.agents/skills/antigravity/scripts/chat_mode.py" setup
```

然后在新开的 Codex 聊天里发一次 `启用 Antigravity`，之后直接发消息。绑定只对该本地聊天生效，重新打开仍可续聊。输入 `退出 Antigravity` 即可切回 Codex。上述安装命令会在用户级 Codex 指令中加入一个单独的会话模式区块。

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

### 独立 Codex 会话

分工时需能从终端运行 `codex`。

- `$antigravity 分工执行 消息` 启动独立的只读会话，共用当前目录。
- `$antigravity 分工实施 消息` 从主分支基线为各写入任务创建独立 Git worktree 和分支。

每次分工执行一轮派单、实施和复审，保留各 Codex 会话 ID 和本地结果。 [分工说明](skills/antigravity/references/coordination.md)

## 验证

`python3 -B -m unittest discover -s tests -v` 运行本机夹具测试，覆盖自动登记、原生回传协议、会话续接、跨项目隔离和分工。夹具使用合成数据模拟 Antigravity Desktop。

[Codex 技能文档](https://learn.chatgpt.com/docs/build-skills) · [Antigravity Sidecar 文档](https://antigravity.google/docs/sidecars) · [Antigravity Hooks 文档](https://antigravity.google/docs/hooks)
