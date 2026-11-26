#!/usr/bin/env python3
"""Opt a single local Codex chat into plain-message Antigravity relay."""
import argparse
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import uuid

BEGIN = "<!-- ANTIGRAVITY_CHAT_MODE_START -->"
END = "<!-- ANTIGRAVITY_CHAT_MODE_END -->"


def codex_home():
    configured = os.environ.get("CODEX_HOME")
    return Path(configured).expanduser().resolve() if configured else Path.home() / ".codex"


def binding():
    thread = os.environ.get("CODEX_THREAD_ID", "")
    try:
        key = str(uuid.UUID(thread))
    except ValueError:
        return None
    return codex_home() / "antigravity-chat-mode" / key


def instructions(bridge):
    command = shlex.join([
        sys.executable, "-B", str(Path(__file__).resolve()), "--bridge", str(bridge),
    ])
    return f"""{BEGIN}
## Antigravity 会话模式

Apply this section only to direct user messages in the current local Codex chat.

- When the user explicitly says `启用 Antigravity` (or asks to turn this chat into an Antigravity chat), run `{command} enable`. Confirm the mode is enabled and that they can send a normal message. Activation is local and takes effect for subsequent turns.
- When the user explicitly says `退出 Antigravity` (or asks to turn this mode off), run `{command} disable`. Confirm that ordinary messages go to Codex again.
- Handle an explicit `$antigravity` skill invocation through the usual skill. For other direct user turns, use the mode established by the latest successful enable, disable, or status call in this chat. If the mode is unknown, including after resuming without that context, run `{command} status`. A `codex` result means continue the normal request.
- If status is `antigravity`, pass the user's current message alone, preserving wording and line breaks, via literal stdin to `{command} send`. Save the resulting ticket, then run `{command} receive <ticket>`. Wait for the native Antigravity reply and continue with the same ticket on a timeout. On success, copy the complete stdout including its `Antigravity：` source label. The user's requested reply format applies to the comment from Antigravity after the label. Report an observed delivery error if the relay fails. In this mode ordinary messages are for Antigravity.
{END}
"""


def setup(bridge):
    if not bridge.is_file():
        raise ValueError(f"找不到 Antigravity 桥接脚本：{bridge}。")
    directory = codex_home()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    override = directory / "AGENTS.override.md"
    path = (override if override.is_file() and override.stat().st_size > 0
            else directory / "AGENTS.md").resolve()
    original = path.read_bytes() if path.is_file() else b""
    existing = original.decode("utf-8")
    section = instructions(bridge)
    if BEGIN in existing or END in existing:
        if existing.count(BEGIN) != 1 or existing.count(END) != 1 or existing.index(BEGIN) >= existing.index(END):
            raise ValueError(f"会话模式标记不完整，请检查 {path}。")
        before, _, remainder = existing.partition(BEGIN)
        _, _, after = remainder.partition(END)
        updated = before + section.rstrip("\n") + after
    else:
        separator = "" if not existing else "\n" if existing.endswith("\n") else "\n\n"
        updated = existing + separator + section
    if updated != existing:
        mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".antigravity-chat-", delete=False) as stream:
            temp = Path(stream.name)
            try:
                stream.write(updated)
                stream.flush()
                os.fsync(stream.fileno())
                temp.chmod(mode)
                current = path.read_bytes() if path.is_file() else b""
                if current != original:
                    raise ValueError(f"{path} 刚被修改，请重试。")
                temp.replace(path)
            finally:
                temp.unlink(missing_ok=True)
    print(f"Antigravity 会话模式已登记在 {path}。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bridge", type=Path, default=Path(__file__).with_name("bridge.py"))
    commands = parser.add_subparsers(dest="action", required=True)
    for name in ("setup", "status", "enable", "disable", "send"):
        commands.add_parser(name)
    receiving = commands.add_parser("receive")
    receiving.add_argument("ticket")
    receiving.add_argument("--timeout", type=int)
    args = parser.parse_args()
    bridge = args.bridge.expanduser().resolve()
    marker = binding()
    try:
        if args.action == "setup":
            setup(bridge)
        elif args.action == "status":
            print("antigravity" if marker and marker.is_file() else "codex")
        elif marker is None:
            raise ValueError("无法识别当前 Codex 聊天，请从本机 Codex 聊天调用。")
        elif args.action == "enable":
            if not bridge.is_file():
                raise ValueError(f"找不到 Antigravity 桥接脚本：{bridge}。")
            marker.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            marker.touch(mode=0o600, exist_ok=True)
            print("Antigravity 模式已启用。")
        elif args.action == "disable":
            marker.unlink(missing_ok=True)
            print("Antigravity 模式已退出。")
        elif not marker.is_file():
            raise ValueError("当前聊天还没有启用 Antigravity 模式。")
        else:
            command = [sys.executable, "-B", str(bridge), args.action]
            if args.action == "receive":
                command.append(args.ticket)
                if args.timeout is not None:
                    command.extend(("--timeout", str(args.timeout)))
                result = subprocess.run(command, stdout=subprocess.PIPE)
                if result.returncode == 0:
                    spacing = b"\n\n" if b"\n" in result.stdout.rstrip(b"\n") else b""
                    sys.stdout.buffer.write("Antigravity：".encode("utf-8") + spacing)
                sys.stdout.buffer.write(result.stdout)
                return result.returncode
            return subprocess.run(command).returncode
    except (OSError, ValueError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
