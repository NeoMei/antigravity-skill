"""Read only completed user-facing replies from Antigravity's native transcript."""
import json
import re

SIDE_CAR_MESSAGE = re.compile(
    r"(?m)^\[Message\] timestamp=[^\n]* sender=system priority=[^\n]* content="
)


def transcript_path(state, conversation_id):
    app_data = state.parents[3]
    return app_data / "brain" / conversation_id / ".system_generated" / "logs" / "transcript.jsonl"


def transcript_rows(path):
    try:
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict) and isinstance(row.get("step_index"), int):
                    yield row
    except FileNotFoundError:
        return


def last_step(state, conversation_id):
    if conversation_id is None:
        return -1
    return max((row["step_index"] for row in transcript_rows(
        transcript_path(state, conversation_id))), default=-1)


def user_body(row):
    text = row.get("content", "")
    if not isinstance(text, str):
        return None
    if row.get("type") == "USER_INPUT" and text.startswith("<USER_REQUEST>\n"):
        return text.removeprefix("<USER_REQUEST>\n").rpartition("\n</USER_REQUEST>")[0]
    if row.get("type") == "SYSTEM_MESSAGE":
        match = SIDE_CAR_MESSAGE.search(text)
        if match:
            return text[match.end():].removesuffix("\n</SYSTEM_MESSAGE>")
    return None


def finished_answer(state, receipt, body, stopped):
    if stopped["at_ns"] < receipt["sent_at_ns"]:
        return None
    path = transcript_path(state, receipt["conversation_id"])
    full = path.with_name("transcript_full.jsonl")
    if full.is_file():
        path = full
    matched = False
    answer = None
    for row in transcript_rows(path):
        if row["step_index"] <= receipt["after_step"]:
            continue
        message = user_body(row)
        if message is not None:
            if matched:
                break
            matched = message.rstrip() == body.rstrip()
        elif (matched and row.get("type") == "PLANNER_RESPONSE"
              and row.get("source") == "MODEL" and row.get("status") == "DONE"
              and not row.get("tool_calls")
              and isinstance(row.get("content"), str) and row["content"].strip()):
            answer = {"answer": row["content"], "model": row.get("model"),
                      "step_index": row["step_index"]}
    if matched and stopped["reason"].lower() in {"error", "max_steps_exceeded", "cancelled", "canceled"}:
        raise ValueError(f"Antigravity 已结束本轮，状态为 {stopped['reason']}。")
    return answer
