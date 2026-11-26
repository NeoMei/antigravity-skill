#!/usr/bin/env python3
"""Pass Codex requests to Antigravity's native agentapi from a Sidecar."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

import native_reply
import task_state
import workspace_routes
from local_state import UUID as CONVERSATION_ID, locked, read_json, write_json

BRIDGE = Path(__file__).resolve()
STATE = Path.home() / ".gemini/antigravity/sidecar_data/codex-antigravity/data/dispatch"
CONFIG = Path.home() / ".gemini/config"
REQUEST_NAME = re.compile(r"message-[0-9]{6}\.request\.json")
MESSAGE_NAME = re.compile(r"message-([0-9]{6})")
MAX_BODY = 16000


def read_body():
    body = sys.stdin.read(MAX_BODY + 1)
    if not body.strip() or len(body) > MAX_BODY or "\0" in body:
        raise ValueError(f"请通过 stdin 提供 1–{MAX_BODY} 字的请求正文。")
    return body


def current_thread():
    thread = os.environ.get("CODEX_THREAD_ID", "")
    if not CONVERSATION_ID.fullmatch(thread):
        raise ValueError("无法获取当前 Codex 聊天 ID，请从 Codex 聊天发起。")
    return thread.lower()


def collect_answer(state, channel, stage, receipt):
    request = read_json(channel / f"{stage}.request.json")
    cache = channel / f"{stage}.native-reply.json"
    if cache.is_file():
        answer = read_json(cache)["answer"]
    else:
        stopped_path = channel / f"{stage}.stop.{receipt['conversation_id']}.json"
        if not stopped_path.is_file():
            return None
        stopped = read_json(stopped_path)
        if stopped.get("workspace") != request["workspace"]:
            raise ValueError("Antigravity 实际工作目录与 Codex 目录不同。")
        result = native_reply.finished_answer(state, receipt, request["prompt"], stopped)
        if result is None:
            return None
        write_json(cache, result, replace=True)
        answer = result["answer"]
    if answer is not None:
        task_state.record_answer(task_path(state, channel, request), request, stage, answer)
    return answer


def task_path(state, channel, request):
    owner = request.get("task_owner", channel.name)
    if not CONVERSATION_ID.fullmatch(owner):
        raise ValueError("协作任务所属聊天 ID 无效。")
    return task_state.path_for(state / owner, request["task_id"])


def same_route(request, route):
    return request["workspace"] == str(route.workspace) and request["project_id"] == route.id


def latest_request(requests, route):
    for path in reversed(requests):
        request = read_json(path)
        if same_route(request, route):
            return path.name.split(".")[0], request
    return None, {}


def send(args):
    thread = current_thread()
    body = read_body()
    if args.task and not args.reopen:
        raise ValueError("指定任务 ID 时需要使用 --reopen。")
    route = workspace_routes.match(args.config, Path.cwd())
    workspace_routes.check_setup(args.config, args.state, BRIDGE)
    workspace_routes.ensure_sidecar(args.config, args.state, route, BRIDGE)
    channel = args.state / thread
    channel.mkdir(mode=0o700, parents=True, exist_ok=True)
    with locked(channel / ".send.lock"):
        requests = sorted(channel.glob("message-??????.request.json"))
        number = 1
        conversation_id = None
        task_id = None
        task_owner = thread
        if requests:
            latest = requests[-1].name.split(".")[0]
            number = int(MESSAGE_NAME.fullmatch(latest)[1]) + 1
            previous, last = latest_request(requests, route)
            keep_task = not args.new and previous is not None
            if keep_task:
                task_id = last.get("task_id")
                task_owner = last.get("task_owner", thread)
            if keep_task and not args.reopen:
                ticket = f"{thread}/{previous}"
                receipt_path = channel / f"{previous}.receipt.json"
                receipt = read_json(receipt_path) if receipt_path.is_file() else {}
                if receipt.get("status") == "error":
                    conversation_id = last.get("conversation_id")
                elif receipt.get("status") == "sent" and collect_answer(
                        args.state, channel, previous, receipt):
                    conversation_id = receipt["conversation_id"]
                else:
                    raise ValueError(f"上一条消息尚未完成，可继续读取 {ticket}。")
        if args.task:
            task_id = args.task
            task_owner = task_state.find_owner(args.state, task_id, route)
        if args.reopen and not task_id:
            raise ValueError("此目录下没有可恢复的当前任务状态。")
        if number > 999999:
            raise ValueError("当前聊天的消息序号已用完。")
        stage = f"message-{number:06d}"
        task_id = task_id or str(uuid.uuid4())
        task = {"task_id": task_id, "task_owner": task_owner}
        snapshot = task_state.prepare(task_path(args.state, channel, task), task_id, route, args.mode, body)
        prompt = task_state.prompt_for(snapshot, args.mode, body)
        if len(prompt.encode("utf-8")) > 120000:
            raise ValueError("任务状态和本轮正文超过本机投递长度，请精简已确认状态或正文。")
        write_json(channel / f"{stage}.request.json", {
            "title": "Codex → Antigravity",
            "body": body,
            "prompt": prompt,
            "conversation_id": conversation_id,
            "project_id": route.id,
            "workspace": str(route.workspace),
            **task,
            "mode": args.mode,
            "context_revision": snapshot["revision"],
            "submitted_at_ns": time.time_ns(),
        })
    return f"{thread}/{stage}"


def context(args):
    channel = args.state / current_thread()
    route = workspace_routes.match(args.config, Path.cwd())
    if not channel.is_dir():
        raise ValueError("此目录下没有当前任务状态，请先发起 Antigravity 会话。")
    with locked(channel / ".send.lock"):
        requests = sorted(channel.glob("message-??????.request.json"))
        _, request = latest_request(requests, route)
        if not request.get("task_id"):
            raise ValueError("此目录下没有当前任务状态，请先发起 Antigravity 会话。")
        path = task_path(args.state, channel, request)
        value = (task_state.update_confirmed(path, json.loads(read_body()))
                 if args.update else read_json(path))
    print(json.dumps(value, ensure_ascii=False, indent=2))


def receive(args):
    thread, _, stage = args.ticket.partition("/")
    if thread != current_thread() or not MESSAGE_NAME.fullmatch(stage):
        raise ValueError("当前 Codex 聊天的消息编号无效。")
    channel = args.state / thread
    if not (channel / f"{stage}.request.json").is_file():
        raise ValueError("找不到这条 Antigravity 消息。")
    receipt_path = channel / f"{stage}.receipt.json"
    deadline = time.monotonic() + args.timeout
    while True:
        if receipt_path.is_file():
            receipt = read_json(receipt_path)
            status = receipt["status"]
            if status in {"error", "uncertain"}:
                raise ValueError(f"Antigravity 投递状态 {status}。{receipt.get('detail', '')}")
            if status == "sent":
                answer = collect_answer(args.state, channel, stage, receipt)
                if answer is not None:
                    return answer
        if time.monotonic() >= deadline:
            raise ValueError(f"Antigravity 尚未回信，可继续读取 {args.ticket}。")
        time.sleep(0.5)


def find_conversation(events, prompt, previous_events):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        ids = set()
        for event_path in events.glob("*.json"):
            if event_path in previous_events:
                continue
            try:
                event = read_json(event_path)
            except (OSError, ValueError):
                continue
            record = event.get("payload", {}).get("newConversation", {})
            conversation_id = record.get("conversationId", "")
            if (not event.get("error") and record.get("prompt") == prompt
                    and CONVERSATION_ID.fullmatch(conversation_id)):
                ids.add(conversation_id)
        if ids:
            return ids.pop() if len(ids) == 1 else None
        time.sleep(0.25)
    return None


def record_stop(state, config):
    event = json.load(sys.stdin)
    conversation_id = event.get("conversationId", "")
    mounts = [Path(path).resolve() for path in event.get("workspacePaths", [])]
    if (event.get("fullyIdle") and CONVERSATION_ID.fullmatch(conversation_id) and len(mounts) == 1
            and workspace_routes.managed_workspace(config, mounts[0])):
        reported = Path(event.get("transcriptPath", "")).expanduser().resolve()
        expected = native_reply.transcript_path(state, conversation_id).resolve()
        if reported in {expected, expected.with_name("transcript_full.jsonl")}:
            record_turn_stop(state, conversation_id, str(mounts[0]), event)
    print("{}")


def record_turn_stop(state, conversation_id, workspace, event):
    stopped_at = time.time_ns()
    for receipt_file in state.glob("*/message-*.receipt.json"):
        stage = receipt_file.name.split(".")[0]
        marker = receipt_file.with_name(f"{stage}.stop.{conversation_id}.json")
        if marker.exists() or receipt_file.with_name(f"{stage}.native-reply.json").exists():
            continue
        receipt = read_json(receipt_file)
        if (receipt.get("status") not in {"dispatching", "sent"}
                or receipt.get("workspace") != workspace
                or receipt.get("conversation_id") not in {None, conversation_id}
                or stopped_at < receipt.get("sent_at_ns", stopped_at)):
            continue
        request = read_json(receipt_file.with_name(f"{stage}.request.json"))
        start = native_reply.stopped_turn(state, conversation_id, request["prompt"], receipt["after_step"])
        if start is None:
            continue
        try:
            # A new conversation can finish before agentapi returns its ID. The
            # receiver uses only the marker for the ID confirmed by agentapi.
            write_json(marker, {"at_ns": stopped_at, "turn_step": start, "workspace": workspace,
                                "reason": event.get("terminationReason", "")})
        except FileExistsError:
            pass


def dispatch(state, packet, stage, events, request):
    receipt = packet.with_name(f"{stage}.receipt.json")
    if receipt.exists():
        return
    prompt = request["prompt"]
    conversation_id = request["conversation_id"]
    if conversation_id is None:
        command = ["agentapi", "new-conversation", f"--title={request['title']}", "--", prompt]
    else:
        if not CONVERSATION_ID.fullmatch(conversation_id):
            raise ValueError("原始会话 ID 无效。")
        command = ["agentapi", "send-message", conversation_id, prompt]

    metadata = {"after_step": native_reply.last_step(state, conversation_id), "sent_at_ns": time.time_ns(),
                "workspace": request["workspace"]}
    try:
        write_json(receipt, {"status": "dispatching", "conversation_id": conversation_id, **metadata})
    except FileExistsError:
        return
    try:
        previous_events = set(events.glob("*.json")) if conversation_id is None else set()
        result = subprocess.run(command, capture_output=True, text=True, timeout=45, check=False)
        if result.returncode:
            write_json(receipt, {"status": "error", "detail": result.stderr.strip()[-1800:],
                                 "exit_code": result.returncode}, replace=True)
            return
        if conversation_id is None:
            conversation_id = find_conversation(events, prompt, previous_events)
            if conversation_id is None:
                write_json(receipt, {"status": "uncertain",
                                     "detail": "Antigravity 未记录唯一的会话 ID。"}, replace=True)
                return
        write_json(receipt, {"status": "sent", "conversation_id": conversation_id, **metadata},
                   replace=True)
    except (OSError, subprocess.TimeoutExpired) as exc:
        write_json(receipt, {"status": "uncertain", "detail": type(exc).__name__}, replace=True)


def serve(args):
    args.state.mkdir(mode=0o700, parents=True, exist_ok=True)
    sidecar = args.sidecar
    project_id = args.project
    if sidecar != workspace_routes.PREFIX + project_id:
        raise ValueError("Sidecar ID 与项目 ID 不匹配。")
    events = args.state.parents[2] / sidecar / "events"
    binding = workspace_routes.bindings(args.config).get(sidecar, {})
    route = workspace_routes.load_project(args.config, project_id)
    if (not binding.get("enabled") or not route
            or not workspace_routes.binding_matches(args.config, sidecar, binding, route)):
        raise ValueError(f"Sidecar {sidecar} 的项目绑定无效。")
    with locked(args.state / f".{sidecar}.worker.lock"):
        process_queue(args, events, binding["projectId"])


def process_queue(args, events, native_project_id):
    sidecar, project_id = args.sidecar, args.project
    print(f"Antigravity Sidecar ready for {project_id}", flush=True)
    while True:
        for packet in sorted(args.state.glob("*/*.request.json")):
            job_id = packet.parent.name
            receipt = packet.with_name(packet.name.replace(".request.", ".receipt."))
            if (packet.is_symlink() or packet.parent.is_symlink()
                    or not CONVERSATION_ID.fullmatch(job_id)
                    or not REQUEST_NAME.fullmatch(packet.name)):
                continue
            try:
                request = read_json(packet)
                if request["project_id"] != project_id:
                    continue
                if receipt.exists():
                    if read_json(receipt).get("status") == "dispatching":
                        write_json(receipt, {"status": "uncertain", "detail":
                                    "Sidecar 在投递期间停止。可用「恢复任务」继续共享状态。"}, replace=True)
                    continue
                active_route = workspace_routes.load_project(args.config, project_id)
                active_binding = workspace_routes.bindings(args.config).get(sidecar, {})
                if (not active_binding.get("enabled") or active_binding.get("projectId") != native_project_id
                        or not active_route or str(active_route.workspace) != request["workspace"]
                        or not workspace_routes.binding_matches(args.config, sidecar, active_binding, active_route)):
                    write_json(receipt, {"status": "error", "detail": "Antigravity 项目的 Sidecar 或目录绑定已变更。"})
                    continue
                dispatch(args.state, packet, packet.name.split(".")[0], events, request)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                print(f"{job_id}/{packet.name}: {type(exc).__name__}", file=sys.stderr, flush=True)
        if args.once:
            return
        time.sleep(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=STATE)
    parser.add_argument("--config", type=Path, default=CONFIG)
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("setup")
    sending = commands.add_parser("send")
    mode = sending.add_mutually_exclusive_group()
    mode.add_argument("--new", action="store_true")
    mode.add_argument("--reopen", action="store_true")
    sending.add_argument("--mode", choices=("ask", "plan", "review"), default="ask")
    sending.add_argument("--task")
    commands.add_parser("context").add_argument("--update", action="store_true")
    commands.add_parser("stop")
    receiving = commands.add_parser("receive")
    receiving.add_argument("ticket")
    receiving.add_argument("--timeout", type=int, default=120)
    serving = commands.add_parser("serve")
    serving.add_argument("--once", action="store_true")
    serving.add_argument("--project", required=True)
    serving.add_argument("--sidecar", required=True)
    args = parser.parse_args()
    try:
        if args.action == "setup":
            workspace_routes.setup(args.config, args.state, BRIDGE)
            print("Antigravity reply hook ready.")
        elif args.action == "serve":
            serve(args)
        elif args.action == "send":
            print(send(args))
        elif args.action == "receive":
            sys.stdout.write(receive(args))
        elif args.action == "context":
            context(args)
        elif args.action == "stop":
            record_stop(args.state, args.config)
    except FileExistsError:
        parser.error("该轮次已提交。")
    except (OSError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
