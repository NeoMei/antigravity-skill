"""Versioned handoff state for a Codex chat's Antigravity task."""
import json

from local_state import UUID, locked, read_json, write_json

FIELDS = {
    "objective", "constraints", "decisions", "branch", "pr",
    "verification", "next_step", "open_questions",
}


def path_for(channel, task_id):
    if not UUID.fullmatch(task_id):
        raise ValueError("协作任务 ID 无效。")
    state_dir = channel / "tasks"
    state_dir.mkdir(mode=0o700, exist_ok=True)
    return state_dir / f"{task_id}.json"


def find_owner(state_root, task_id, route):
    if not UUID.fullmatch(task_id):
        raise ValueError("协作任务 ID 无效。")
    matches = []
    for path in state_root.glob(f"*/tasks/{task_id}.json"):
        owner = path.parent.parent.name
        if not UUID.fullmatch(owner) or path.is_symlink():
            continue
        state = read_json(path)
        if state.get("workspace") == str(route.workspace) and state.get("project_id") == route.id:
            matches.append(owner)
    if len(matches) != 1:
        raise ValueError("此目录下未找到唯一匹配的协作任务。")
    return matches[0]


def change(path, update):
    with locked(path.with_suffix(".lock")):
        state = read_json(path) if path.is_file() else {}
        previous = json.dumps(state, ensure_ascii=False, sort_keys=True)
        update(state)
        if json.dumps(state, ensure_ascii=False, sort_keys=True) != previous:
            state["revision"] = state.get("revision", 0) + 1
            write_json(path, state, replace=True)
        return state


def prepare(path, task_id, route, mode, body):
    def update(state):
        if not state:
            state.update(task_id=task_id, workspace=str(route.workspace), project_id=route.id,
                         confirmed={})
        if mode == "plan" and not state["confirmed"].get("objective"):
            state["confirmed"]["objective"] = body.strip()
        if mode == "review":
            state["implementation_evidence"] = body.strip()

    return change(path, update)


def update_confirmed(path, values):
    if not isinstance(values, dict) or not values or values.keys() - FIELDS:
        raise ValueError(f"请提供任务状态 JSON，可使用字段：{', '.join(sorted(FIELDS))}。")
    if not all(isinstance(value, str) and len(value) <= 12000 for value in values.values()):
        raise ValueError("任务状态各字段须为最多 12000 字的字符串。")
    return change(path, lambda state: state["confirmed"].update(values))


def record_answer(path, request, stage, answer):
    mode = request.get("mode")
    if mode not in {"plan", "review"}:
        return
    field = f"last_{mode}"

    def update(state):
        previous = state.get(field, {})
        ticket_time = request.get("submitted_at_ns", 0)
        existing_time = previous.get("submitted_at_ns", 0)
        if ticket_time > existing_time or (not ticket_time and not existing_time
                                           and previous.get("stage", "") < stage):
            state[field] = {"stage": stage, "based_on_revision": request["context_revision"],
                            "submitted_at_ns": ticket_time, "answer": answer}

    change(path, update)


def prompt_for(state, mode, body):
    if mode == "ask" and not any(state.get(key) for key in
                                  ("confirmed", "implementation_evidence", "last_plan", "last_review")):
        return body
    if mode == "plan":
        request = ("你是本任务的 Antigravity 主控。结合任务状态，指出 Codex 当前应实施的事项和可验证的验收标准。"
                   "由 Codex 负责实施。")
    elif mode == "review":
        request = ("你是本任务的 Antigravity 主控。根据派单和实施证据独立复审，"
                   "给出通过或返工结论、实际核对证据和待修问题。由 Codex 负责修改。")
    else:
        request = "请结合已同步的任务状态回答 Codex 用户的消息。"
    snapshot = json.dumps(state, ensure_ascii=False, indent=2)
    return (
        f"{request} 已确认事项在 confirmed；last_plan 和 last_review 是 Antigravity 的既有答复。"
        "将证据与已确认决定区分开。\n\n"
        f"任务状态（版本 {state['revision']}）\n{snapshot}\n\n"
        f"本轮消息\n{body}"
    )
