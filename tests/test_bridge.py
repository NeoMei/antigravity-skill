import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid

SKILL = Path(__file__).resolve().parents[1] / "skills" / "antigravity"
REPLY = "Antigravity 原文\n\n    Preserve Markdown."
AGENTAPI = r'''
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import uuid

kind, *args = sys.argv[1:]
body = args[-1]
root = Path(os.environ["FIXTURE_APP_DATA"])
sidecar = os.environ["FIXTURE_SIDECAR"]
workspace = os.environ["FIXTURE_WORKSPACE"]
config = Path(os.environ["FIXTURE_CONFIG"])
conversation = str(uuid.uuid4()) if kind == "new-conversation" else args[0]
events = root / "sidecar_data" / sidecar / "events"
events.mkdir(parents=True, exist_ok=True)
entry = ({"newConversation": {"prompt": body, "conversationId": conversation}}
         if kind == "new-conversation" else {"sendMessage": {"content": body, "recipientId": conversation}})
(events / f"{uuid.uuid4()}.json").write_text(json.dumps({"payload": entry}))
with (root / "calls.jsonl").open("a") as calls:
    calls.write(json.dumps([kind, conversation, workspace, body]) + "\n")

logs = root / "brain" / conversation / ".system_generated" / "logs"
logs.mkdir(parents=True, exist_ok=True)
full = logs / "transcript_full.jsonl"
display = logs / "transcript.jsonl"
previous = len(full.read_text().splitlines()) if full.is_file() else 0
before, after = (("<USER_REQUEST>\n", "\n</USER_REQUEST>") if not previous else (
    "[Message] timestamp=2026-01-01T00:00:00Z sender=system priority=HIGH content=",
    "\n</SYSTEM_MESSAGE>",
))
user_type = "USER_INPUT" if not previous else "SYSTEM_MESSAGE"
for transcript, content in ((full, body), (display, body[:15] + " [display abbreviated]")):
    with transcript.open("a") as f:
        rows = [
            {"step_index": previous, "type": user_type, "content": before + content + after},
            {"step_index": previous + 1, "type": "PLANNER_RESPONSE", "source": "MODEL", "status": "DONE",
             "tool_calls": [{"name": "read_file"}], "content": "Intermediate tool step"},
            {"step_index": previous + 2, "type": "PLANNER_RESPONSE", "source": "MODEL", "status": "DONE",
             "content": os.environ["FIXTURE_ANSWER"], "thinking": "Do not relay this trace"},
        ]
        f.write("".join(json.dumps(row) + "\n" for row in rows))
hook = json.loads((config / "hooks.json").read_text())["codex-antigravity-reply"]["Stop"][0]
completed = subprocess.run(shlex.split(hook["command"]), input=json.dumps({
    "conversationId": conversation,
    "fullyIdle": True,
    "workspacePaths": [workspace],
    "transcriptPath": str(full),
    "terminationReason": "NO_TOOL_CALL",
}), text=True, capture_output=True, check=True)
assert completed.stdout.strip() == "{}"
if os.environ.get("FIXTURE_DISABLE_AFTER_SEND"):
    path = config / "config.json"
    settings = json.loads(path.read_text())
    settings["sidecars"][sidecar]["enabled"] = False
    path.write_text(json.dumps(settings))
'''


class BridgeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="antigravity test ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.config = self.root / "configuration"
        self.app = self.root / "app data"
        self.state = self.app / "sidecar_data" / "codex-antigravity" / "data" / "dispatch"
        self.thread = str(uuid.uuid4())
        self.ids = [str(uuid.uuid4()), str(uuid.uuid4())]
        self.workspaces = [self.root / "Workspace One", self.root / "Git Worktree Two"]
        (self.config / "projects").mkdir(parents=True)
        self.config_json = self.config / "config.json"
        self.config_json.write_text(json.dumps({"sidecars": {"existing": {"enabled": False}}}))
        self.hooks_json = self.config / "hooks.json"
        self.hooks_json.write_text(json.dumps({"existing": {"enabled": False}}))
        for i, (project_id, workspace) in enumerate(zip(self.ids, self.workspaces)):
            workspace.mkdir()
            folder = {"folderUri": workspace.as_uri()}
            resource = folder if i == 0 else {"gitFolder": {**folder, "defaultBranch": "main"}}
            (self.config / "projects" / f"{project_id}.json").write_text(json.dumps({
                "id": project_id, "name": workspace.name,
                "projectResources": {"resources": [resource]},
            }))
        installed = self.root / "skills installed" / "antigravity"
        shutil.copytree(SKILL, installed)
        self.bridge = installed / "scripts" / "bridge.py"
        api = self.root / "agentapi"
        api.write_text(f"#!{sys.executable}\n" + AGENTAPI)
        api.chmod(0o700)
        self.env = {
            **os.environ, "PATH": str(self.root) + os.pathsep + os.environ["PATH"],
            "CODEX_THREAD_ID": self.thread,
            "FIXTURE_CONFIG": str(self.config), "FIXTURE_APP_DATA": str(self.app),
            "FIXTURE_ANSWER": REPLY, "PYTHONDONTWRITEBYTECODE": "1",
        }

    def run_bridge(self, *args, cwd=None, body=None, env=None, check=True):
        command = [sys.executable, "-B", str(self.bridge), "--config", str(self.config),
                   "--state", str(self.state), *args]
        result = subprocess.run(command, cwd=cwd or self.workspaces[0], input=body, text=True,
                                capture_output=True, env=env or self.env, timeout=15)
        if check:
            self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def serve(self, i, **env):
        project = self.ids[i]
        sidecar = f"codex-antigravity-{project}"
        manifest = json.loads((self.config / "sidecars" / sidecar / "sidecar.json").read_text())
        result = subprocess.run([manifest["command"], *manifest["args"], "--once"], text=True,
                                capture_output=True, env={**self.env, "FIXTURE_SIDECAR": sidecar,
                                "FIXTURE_WORKSPACE": str(self.workspaces[i]), **env}, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def record(self, ticket, name):
        thread, stage = ticket.split("/")
        return json.loads((self.state / thread / f"{stage}.{name}.json").read_text())

    def test_setup_preserves_other_settings_and_requires_matching_folder(self):
        missing = self.run_bridge("send", body="hello", check=False)
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("setup", missing.stderr)
        self.run_bridge("setup")
        installed = self.hooks_json.read_bytes()
        self.run_bridge("setup")
        self.assertEqual(self.hooks_json.read_bytes(), installed)
        self.assertEqual(json.loads(installed)["existing"], {"enabled": False})
        unknown = self.root / "unregistered"
        unknown.mkdir()
        rejected = self.run_bridge("send", cwd=unknown, body="not delivered", check=False)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn(str(unknown), rejected.stderr)
        self.assertEqual(json.loads(self.config_json.read_text())["sidecars"], {"existing": {"enabled": False}})
        self.assertFalse(self.state.exists())

    def test_setup_can_coexist_with_a_legacy_sidecar_project(self):
        label = f"accowork-{self.ids[0]}"
        projected = str(uuid.uuid4())
        old = json.loads((self.config / "projects" / f"{self.ids[0]}.json").read_text())
        (self.config / "projects" / f"{projected}.json").write_text(
            json.dumps({**old, "id": projected, "name": label}))
        settings = json.loads(self.config_json.read_text())
        settings["sidecars"][label] = {"enabled": True, "projectId": self.ids[0]}
        self.config_json.write_text(json.dumps(settings))
        self.run_bridge("setup")
        ticket = self.run_bridge("send", body="Use the actual registered project").stdout.strip()
        self.assertEqual(self.record(ticket, "request")["project_id"], self.ids[0])
        self.assertEqual(json.loads(self.config_json.read_text())["sidecars"][label],
                         settings["sidecars"][label])

    def test_native_reply_shared_context_and_workspace_isolation(self):
        self.run_bridge("setup")
        plan = self.run_bridge("send", "--mode", "plan", body="Plan alpha").stdout.strip()
        pending = self.run_bridge("send", body="duplicate", check=False)
        self.assertNotEqual(pending.returncode, 0)
        self.serve(0)
        self.serve(0)
        initial = self.record(plan, "receipt")
        session = initial["conversation_id"]
        logs = self.app / "brain" / session / ".system_generated" / "logs"
        with (logs / "transcript_full.jsonl").open("a") as stream:
            stream.write(json.dumps({
                "step_index": 3, "type": "USER_INPUT",
                "content": "<USER_REQUEST>\nManual follow-up\n</USER_REQUEST>",
            }) + "\n")
        self.run_bridge("stop", body=json.dumps({
            "conversationId": session, "fullyIdle": True,
            "workspacePaths": [str(self.workspaces[0])],
            "transcriptPath": str(logs / "transcript_full.jsonl"), "terminationReason": "error",
        }))
        self.assertEqual(self.run_bridge("receive", plan).stdout, REPLY)
        self.run_bridge("context", "--update", body='{"verification":"15 bytes verified"}')
        review = self.run_bridge("send", "--mode", "review", body="Review evidence").stdout.strip()
        self.serve(0)
        self.assertEqual(self.run_bridge("receive", review).stdout, REPLY)
        first = self.record(plan, "receipt")
        self.assertEqual(self.record(review, "receipt")["conversation_id"], first["conversation_id"])
        self.assertIn("15 bytes verified", self.record(review, "request")["prompt"])

        state = json.loads(self.run_bridge("context").stdout)
        self.assertEqual(state["last_review"]["answer"], REPLY)
        new_env = {**self.env, "CODEX_THREAD_ID": str(uuid.uuid4())}
        resumed = self.run_bridge("send", "--reopen", "--task", state["task_id"],
                                  body="Resume", env=new_env).stdout.strip()
        self.serve(0)
        self.assertEqual(self.run_bridge("receive", resumed, env=new_env).stdout, REPLY)
        self.assertEqual(json.loads(self.run_bridge("context", env=new_env).stdout), state)
        self.assertNotEqual(self.record(resumed, "receipt")["conversation_id"], first["conversation_id"])

        other = self.run_bridge("send", body="Plain question", cwd=self.workspaces[1]).stdout.strip()
        self.serve(0)
        self.assertFalse((self.state / self.thread / "message-000003.receipt.json").exists())
        self.serve(1)
        self.assertEqual(self.run_bridge("receive", other, cwd=self.workspaces[1]).stdout, REPLY)
        self.assertEqual(self.record(other, "request")["prompt"], "Plain question")
        self.assertNotEqual(self.record(other, "request")["task_id"], state["task_id"])
        calls = [json.loads(line) for line in (self.app / "calls.jsonl").read_text().splitlines()]
        self.assertEqual([call[0] for call in calls],
                         ["new-conversation", "send-message", "new-conversation", "new-conversation"])
        self.assertEqual(calls[-1][2], str(self.workspaces[1]))
        settings = json.loads(self.config_json.read_text())["sidecars"]
        self.assertEqual(settings[f"codex-antigravity-{self.ids[1]}"]["schemaVersion"], 1)
        self.assertEqual(json.loads(self.run_bridge("context").stdout), state)
        back = self.run_bridge("send", body="Back in the original directory").stdout.strip()
        self.assertEqual(self.record(back, "request")["conversation_id"], first["conversation_id"])

    def test_moving_installation_refreshes_the_hook_and_sidecar(self):
        self.run_bridge("setup")
        ticket = self.run_bridge("send", body="Before moving").stdout.strip()
        self.serve(0)
        self.assertEqual(self.run_bridge("receive", ticket).stdout, REPLY)
        moved = self.root / "new skill location" / "antigravity"
        moved.parent.mkdir()
        shutil.move(self.bridge.parents[1], moved)
        self.bridge = moved / "scripts" / "bridge.py"
        self.run_bridge("setup")
        ticket = self.run_bridge("send", body="After moving").stdout.strip()
        self.serve(0)
        self.assertEqual(self.run_bridge("receive", ticket).stdout, REPLY)
        self.assertEqual(json.loads(self.hooks_json.read_text())["existing"], {"enabled": False})

    def test_worker_rechecks_binding_and_marks_interrupted_delivery(self):
        self.run_bridge("setup")
        tickets = []
        for thread in sorted([self.thread, str(uuid.uuid4())]):
            env = {**self.env, "CODEX_THREAD_ID": thread}
            ticket = self.run_bridge("send", body="Queued request", env=env).stdout.strip()
            tickets.append(ticket)
        self.serve(0, FIXTURE_DISABLE_AFTER_SEND="1")
        self.assertEqual(self.record(tickets[0], "receipt")["status"], "sent")
        self.assertEqual(self.record(tickets[1], "receipt")["status"], "error")
        self.assertEqual(len((self.app / "calls.jsonl").read_text().splitlines()), 1)
        settings = json.loads(self.config_json.read_text())
        settings["sidecars"][f"codex-antigravity-{self.ids[0]}"]["enabled"] = True
        self.config_json.write_text(json.dumps(settings))
        thread = str(uuid.uuid4())
        env = {**self.env, "CODEX_THREAD_ID": thread}
        ticket = self.run_bridge("send", body="Interrupted request", env=env).stdout.strip()
        stage = ticket.split("/")[1]
        (self.state / thread / f"{stage}.receipt.json").write_text('{"status":"dispatching"}')
        self.serve(0)
        self.assertEqual(self.record(ticket, "receipt")["status"], "uncertain")
        self.assertEqual(len((self.app / "calls.jsonl").read_text().splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
