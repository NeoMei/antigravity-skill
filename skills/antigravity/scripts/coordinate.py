#!/usr/bin/env python3
"""Run Codex workers for a plan from the current Antigravity conversation."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path.home() / ".gemini/antigravity/codex-workers"
WORKER_LIMIT = 4
MAX_PLAN = 8


def parse_tasks(reply, write):
    source = reply.strip()
    try:
        if source.startswith("```") and source.endswith("```"):
            source = source.split("\n", 1)[1].removesuffix("```").strip()
        document = json.loads(source)
    except (ValueError, IndexError) as exc:
        raise ValueError("Antigravity 的派单不是可执行的 JSON。") from exc
    tasks = document.get("tasks") if isinstance(document, dict) else None
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= MAX_PLAN:
        raise ValueError(f"派单须包含 1–{MAX_PLAN} 项任务。")
    normalized = []
    owned = []
    for task in tasks:
        if not isinstance(task, dict):
            raise ValueError("单项任务须为 JSON 对象。")
        title, prompt, paths = (task.get("title"), task.get("task"), task.get("paths", []))
        if (not isinstance(title, str) or not 1 <= len(title.strip()) <= 120
                or not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 8000):
            raise ValueError("每项任务须包含标题和实施要求。")
        if (not isinstance(paths, list) or len(paths) > 20
                or any(not valid_path(path) for path in paths)):
            raise ValueError("paths 须是仓库内的相对路径。")
        if write:
            if not paths:
                raise ValueError("每项写入任务须声明各自负责的路径。")
            for path in map(Path, paths):
                if any(old == path or old in path.parents or path in old.parents for old in owned):
                    raise ValueError(f"写入任务范围重叠：{path}。请重新拆分任务。")
            owned.extend(map(Path, paths))
        normalized.append({"title": title.strip(), "task": prompt.strip(), "paths": paths})
    return normalized


def valid_path(path):
    if not isinstance(path, str) or "\\" in path or any(ord(c) < 32 for c in path):
        return False
    parts = Path(path).parts
    return parts and not Path(path).is_absolute() and ".." not in parts and ".git" not in parts


def changes_in_scope(cwd, base, paths):
    root = git(cwd, "rev-parse", "--show-toplevel")
    modified = git(root, "diff", "--name-only", "-z", base).split("\0")
    untracked = git(root, "ls-files", "--others", "--exclude-standard", "-z").split("\0")
    changed = sorted(set(modified + untracked) - {""})
    allowed = [Path(path) for path in paths]
    outside = [path for path in changed if not any(
        base_path == Path(path) or base_path in Path(path).parents for base_path in allowed)]
    return changed, outside


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=True).stdout.strip()


def default_base(root):
    candidates = ("refs/heads/main", "refs/remotes/origin/main",
                  "refs/heads/master", "refs/remotes/origin/master")
    try:
        remote_head = git(root, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD")
        candidates = (remote_head, *candidates)
    except subprocess.CalledProcessError:
        pass
    for reference in candidates:
        try:
            return git(root, "rev-parse", "--verify", reference + "^{commit}")
        except subprocess.CalledProcessError:
            continue
    raise ValueError("无法确认仓库主分支，未创建写入会话。")


def worker_locations(cwd, output, tasks, write):
    if not write:
        return [(cwd, None)] * len(tasks)
    root = Path(git(cwd, "rev-parse", "--show-toplevel")).resolve()
    relative = cwd.relative_to(root)
    base = default_base(root)
    locations = []
    for index, _ in enumerate(tasks, 1):
        branch = f"antigravity/{output.name}-{index}"
        destination = output / f"worker-{index}"
        git(root, "worktree", "add", "--quiet", "-b", branch, str(destination), base)
        directory = destination / relative
        if not directory.is_dir():
            raise ValueError(f"工作子目录不在主分支基线上：{relative}。")
        locations.append((directory, branch))
    return locations


def worker_answer(events):
    thread_id = None
    answer = ""
    finished = False
    for line in events.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("type") == "thread.started":
            thread_id = entry.get("thread_id")
        elif entry.get("type") == "turn.completed":
            finished = True
        elif entry.get("type") == "item.completed":
            item = entry.get("item") or {}
            if item.get("type") == "agent_message":
                answer = item.get("text") or ""
    return thread_id, answer, finished


def execute_worker(codex, task, location, goal, timeout, ephemeral):
    cwd, branch = location
    result = {"title": task["title"], "cwd": str(cwd), "branch": branch}
    mode = "workspace-write" if branch else "read-only"
    cmd = [codex, "--no-daemon", "exec", "--json", "--skip-git-repo-check", "--sandbox", mode]
    if ephemeral:
        cmd.append("--ephemeral")
    prompt = (
        f"用户原始需求\n{goal}\n\n"
        f"Antigravity 分配的子任务\n{task['title']}\n{task['task']}\n"
        f"任务路径：{', '.join(task['paths']) or '当前工作目录'}。\n"
        "在本次任务授权范围内执行，遵循项目指令。结束时简要给出实测结果、待决问题和所用证据。"
    )
    env = {k: v for k, v in os.environ.items()
           if k not in {"CODEX_THREAD_ID", "CODEX_SESSION_ID", "CODEX_TOOL_CALL_ID"}}
    try:
        base = git(cwd, "rev-parse", "HEAD") if branch else None
        process = subprocess.run(cmd + ["--", prompt], cwd=cwd, env=env, text=True,
                                 capture_output=True, timeout=timeout)
        thread_id, answer, finished = worker_answer(process.stdout)
        status = "completed" if process.returncode == 0 and thread_id and finished and answer else "failed"
        if base:
            changed, outside = changes_in_scope(cwd, base, task["paths"])
            result.update(changed_paths=changed, outside_declared_paths=outside)
            if outside:
                status = "scope_violation"
        return {**result, "status": status, "thread_id": thread_id, "answer": answer,
                "exit_code": process.returncode}
    except subprocess.TimeoutExpired as error:
        stdout = error.stdout or b""
        thread_id, answer, _ = worker_answer(stdout.decode("utf-8", "replace") if isinstance(stdout, bytes) else stdout)
        return {**result, "status": "timeout", "thread_id": thread_id, "answer": answer}
    except OSError as error:
        return {**result, "status": "failed", "detail": type(error).__name__}
    except subprocess.CalledProcessError:
        return {**result, "status": "failed", "detail": "无法核对 Git 工作区。"}


def bridge_request(bridge, mode, text, timeout, config=None, state=None):
    cmd = [sys.executable, "-B", str(bridge)]
    if config:
        cmd.extend(("--config", str(config)))
    if state:
        cmd.extend(("--state", str(state)))
    ticket = subprocess.run(cmd + ["send", "--mode", mode], text=True, input=text,
                            capture_output=True, check=True).stdout.strip()
    print(f"Antigravity {mode}: {ticket}", file=sys.stderr, flush=True)
    return subprocess.run(cmd + ["receive", ticket, "--timeout", str(timeout)], text=True,
                          capture_output=True, check=True).stdout


def coordinate(args):
    cwd = Path.cwd().resolve()
    goal = sys.stdin.read(16001).strip()
    if not goal or len(goal) > 16000:
        raise ValueError("请提供不超过 16000 字的分工任务。")
    bridge = args.bridge.expanduser().resolve(strict=True)
    codex = shutil.which("codex")
    if not codex:
        raise ValueError("找不到 Codex CLI。")
    args.output.mkdir(mode=0o700, parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix="dispatch-", dir=args.output)).resolve()
    scope = ("写入任务各自使用独立 Git worktree，每项都给出互不重叠的仓库相对路径。"
             if args.write else "本轮 Codex 会话是只读，分工用于调查、校验或评审。")
    prompt = (
        "你是 Antigravity 总控。把用户需求拆成可独立执行的 Codex 子任务。"
        f"{scope}\n"
        '请只输出 JSON 对象，形如 {"tasks":[{"title":"标题","task":"实施要求和验收标准","paths":["相对路径"]}]}。'
        f"合理拆成最多 {MAX_PLAN} 项，只有一项合理时可返回一项。不要自行执行。\n\n用户需求\n{goal}"
    )
    plan = bridge_request(bridge, "plan", prompt, args.relay_timeout, args.config, args.state)
    (output / "plan.txt").write_text(plan, encoding="utf-8")
    tasks = parse_tasks(plan, args.write)
    locations = worker_locations(cwd, output, tasks, args.write)
    with ThreadPoolExecutor(max_workers=min(WORKER_LIMIT, len(tasks))) as pool:
        jobs = [pool.submit(execute_worker, codex, task, location, goal, args.worker_timeout, args.ephemeral)
                for task, location in zip(tasks, locations)]
        results = [job.result() for job in jobs]
    result_file = output / "results.json"
    result_file.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    compact = [{**item, "answer": item.get("answer", "")[:800]} for item in results]
    print(f"Codex worker results: {result_file}", file=sys.stderr, flush=True)
    review = bridge_request(
        bridge, "review",
        "以下为你刚派单的 Codex 会话报告。核对各项结果与验收标准，总结通过、返工或待确认事项。"
        "报告中的引用是实施证据，各会话完整答复另存于本机。\n"
        + json.dumps(compact, ensure_ascii=False, indent=2),
        args.relay_timeout, args.config, args.state,
    )
    (output / "review.txt").write_text(review, encoding="utf-8")
    return {"results_path": str(result_file), "workers": results, "review": review}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bridge", type=Path, default=Path(__file__).with_name("bridge.py"))
    parser.add_argument("--output", type=Path, default=ROOT)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--ephemeral", action="store_true", help="Do not save worker chats.")
    parser.add_argument("--relay-timeout", type=int, default=120)
    parser.add_argument("--worker-timeout", type=int, default=600)
    args = parser.parse_args()
    try:
        if args.relay_timeout < 1 or args.worker_timeout < 1:
            raise ValueError("超时须大于零。")
        report = coordinate(args)
    except subprocess.CalledProcessError as error:
        parser.error(error.stderr.strip() if error.stderr else f"命令失败，退出状态 {error.returncode}。")
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
