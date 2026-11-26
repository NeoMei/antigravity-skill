"""Read only completed user-facing replies from Antigravity's native transcript."""
import json
import re

SIDE_CAR_MESSAGE = re.compile(
    r"(?m)^\[Message\] timestamp=[^\n]* sender=system priority=(?P<priority>[^\s]+) content="
)


def transcript_path(state, conversation_id):
    app_data = state.parents[3]
    return app_data / "brain" / conversation_id / ".system_generated" / "logs" / "transcript.jsonl"


def complete_transcript(state, conversation_id):
    path = transcript_path(state, conversation_id)
    full = path.with_name("transcript_full.jsonl")
    return full if full.is_file() else path


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
        complete_transcript(state, conversation_id))), default=-1)


def user_body(row):
    text = row.get("content", "")
    if not isinstance(text, str):
        return None
    if row.get("type") == "USER_INPUT" and text.startswith("<USER_REQUEST>\n"):
        return text.removeprefix("<USER_REQUEST>\n").rpartition("\n</USER_REQUEST>")[0]
    if row.get("type") == "SYSTEM_MESSAGE":
        match = SIDE_CAR_MESSAGE.search(text)
        # agentapi messages use high priority. Background notices share the same envelope.
        if match and match["priority"] in {"HIGH", "MESSAGE_PRIORITY_HIGH"}:
            return text[match.end():].removesuffix("\n</SYSTEM_MESSAGE>")
    return None


def stopped_turn(state, conversation_id, body, after_step):
    path = complete_transcript(state, conversation_id)
    start = None
    for row in transcript_rows(path):
        message = user_body(row)
        if row["step_index"] <= after_step or message is None:
            continue
        if start is not None or message.rstrip() != body.rstrip():
            return None
        start = row["step_index"]
    return start


def finished_answer(state, receipt, body, stopped):
    if stopped["at_ns"] < receipt["sent_at_ns"]:
        return None
    path = complete_transcript(state, receipt["conversation_id"])
    start = stopped["turn_step"]
    matched = False
    answer = None
    for row in transcript_rows(path):
        if row["step_index"] < start:
            continue
        message = user_body(row)
        if message is not None:
            if matched:
                break
            matched = row["step_index"] == start and message.rstrip() == body.rstrip()
        elif (matched and row.get("type") == "PLANNER_RESPONSE"
              and row.get("source") == "MODEL" and row.get("status") == "DONE"
              and not row.get("tool_calls")
              and isinstance(row.get("content"), str) and row["content"].strip()):
            answer = {"answer": row["content"], "model": row.get("model"),
                      "step_index": row["step_index"]}
    if matched and stopped["reason"].lower() in {"error", "max_steps_exceeded", "cancelled", "canceled"}:
        raise ValueError(f"Antigravity 已结束本轮，状态为 {stopped['reason']}。")
    return answer
