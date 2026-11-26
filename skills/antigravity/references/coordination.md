# Codex worker dispatch

Use this procedure when the user asks Antigravity to launch separate Codex chats and execute assigned subtasks.

- `$antigravity 分工执行 消息` starts read-only Codex workers in the current workspace.
- `$antigravity 分工实施 消息` permits write workers in separate Git worktrees starting from the repository default branch. Use write mode when the user has authorized code changes.
- Ordinary questions, plans, and reviews continue to use the Antigravity bridge.

Resolve `scripts/coordinate.py` and `scripts/bridge.py` relative to the installed Antigravity skill. From the Codex chat working directory, send the user's request on stdin to `python3 -B <coordinate.py> --bridge <bridge.py>`. Add `--write` for write workers. Keep the tool call running through both the worker phase and the Antigravity review.

Antigravity returns a structured assignment. The runner starts one Codex CLI session per task, with up to four running at the same time. Read-only workers share the workspace. Write workers use separate branches and declared path scopes. The runner returns a local result file, the worker thread IDs, and Antigravity's final review from the original master conversation.

Show the worktree and branch for write workers, and link to the Codex worker chats using `codex://threads/<thread-id>` when a thread ID is available. Relay the `review` field with the prefix `Antigravity：`. The regular project PR and merge rules apply to worker changes.

If a run fails after workers have started, use its existing results file and review ticket to recover progress. Launching the whole command again would create a new set of workers.
