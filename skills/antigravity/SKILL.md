---
name: antigravity
description: 'Use when the user invokes $antigravity or asks Antigravity to answer, plan, or review the active local Codex task.'
---

# Antigravity collaboration

Resolve `scripts/bridge.py` relative to this installed `SKILL.md`. Run `python3 -B <absolute-bridge-path>` from the Codex chat's actual workspace directory. The bridge matches the exact local Antigravity project, including an explicitly registered Git worktree. If matching fails, relay the path and registration issue. Keep Antigravity Desktop running.

On first installation or after moving the Skill, run `python3 -B <absolute-bridge-path> setup` to configure the local completion hook and bridge-owned Sidecars. If delivery is uncertain, preserve the ticket and report that the user can use `$antigravity 恢复任务 消息` to carry the saved state into a fresh Antigravity conversation.

## Send and receive

Strip the invocation and one recognized plain-language command. Preserve the remaining user text and paragraph breaks. Pass it to the indicated command through stdin with literal shell quoting. For a command that requires a message, ask for it if absent.

| User input | Bridge command |
| --- | --- |
| `$antigravity 消息` | `send` |
| `$antigravity 派单 消息` | `send --mode plan` |
| `$antigravity 复审 消息` | `send --mode review` |
| `$antigravity 新会话 消息` | `send --new` |
| `$antigravity 恢复任务 消息` | `send --reopen` |
| `$antigravity 恢复任务 任务ID 消息` | `send --reopen --task <任务ID>` |
| `$antigravity 状态` | `context` |

Also honor natural-language requests with the same clear intent. For send commands, keep the ticket and run `receive <ticket>` until the native answer arrives or a concrete failure needs attention. Continue with the same ticket after a timeout. For a long wait, report verified delivery progress in commentary.

The final response for a successful relay consists of `Antigravity：` and the full stdout of `receive`. Copy each line, preserving the original Markdown, punctuation, and link destinations. Put multiline stdout below the label and treat it as a quotation from Antigravity.

## Shared task state

Questions, assignments, and reviews use the active Antigravity conversation for this Codex chat and exact workspace. The bridge attaches the current task version and records Antigravity's assignment and review.

The `context` output includes the task ID. The user can use that ID to resume the same saved state from another Codex chat in the same workspace.

When handing off ongoing Codex work, update confirmed facts that are relevant and authorized for this task. Pass an object of string values on stdin to `context --update`. Supported fields are `objective`, `constraints`, `decisions`, `branch`, `pr`, `verification`, `next_step`, and `open_questions`. Update only fields with new facts. Keep user decisions, pending questions, and Antigravity's own suggestions distinct. If there is no task yet, include the relevant facts in the first message.

For a review, include the implemented change and observed evidence in the message. If the user requests implementation as well, carry it out in Codex under the project's normal rules. Share task-relevant summaries and verified artifacts. Include other chat content only when requested.
