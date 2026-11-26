import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "antigravity" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import coordinate


class CoordinateTest(unittest.TestCase):
    def test_protocol_preserves_scopes_and_rejects_overlapping_write_tasks(self):
        plan = {"tasks": [
            {"title": "One", "task": "Review", "paths": ["src/中文.txt"]},
            {"title": "Two", "task": "Test", "paths": ["tests"]},
        ]}
        parsed = coordinate.parse_tasks("```json\n" + json.dumps(plan) + "\n```", write=True)
        self.assertEqual(parsed, plan["tasks"])
        plan["tasks"][1]["paths"] = ["src"]
        with self.assertRaisesRegex(ValueError, "重叠"):
            coordinate.parse_tasks(json.dumps(plan), write=True)
        plan["tasks"][1]["paths"] = ["src/../x"]
        with self.assertRaisesRegex(ValueError, "相对路径"):
            coordinate.parse_tasks(json.dumps(plan), write=False)

    def test_writes_use_independent_main_worktrees(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            repository = root / "repository"
            subprocess.run(["git", "init", "-q", "-b", "main", str(repository)], check=True)
            coordinate.git(repository, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                           "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null",
                           "commit", "-q", "--allow-empty", "-m", "initialize")
            base = coordinate.git(repository, "rev-parse", "HEAD")
            output = root / "result"
            output.mkdir()
            paths = coordinate.worker_locations(repository, output, [{}, {}], True)
            self.assertNotEqual(paths[0][0], paths[1][0])
            self.assertNotEqual(paths[0][1], paths[1][1])
            for cwd, branch in paths:
                self.assertEqual(coordinate.git(cwd, "rev-parse", "HEAD"), base)
                self.assertEqual(coordinate.git(cwd, "branch", "--show-current"), branch)
            (paths[0][0] / "omitted").write_text("test")
            actual, outside = coordinate.changes_in_scope(paths[0][0], base, ["allowed"])
            self.assertEqual(actual, ["omitted"])
            self.assertEqual(outside, ["omitted"])
            self.assertEqual(coordinate.git(repository, "branch", "--show-current"), "main")

    def test_captures_native_worker_session_and_final_reply(self):
        events = [
            {"type": "thread.started", "thread_id": "thread-worker"},
            {"type": "item.completed", "item": {"type": "command_execution", "text": "skip"}},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "result"}},
            {"type": "turn.completed"},
        ]
        result = subprocess.CompletedProcess([], 0, "\n".join(map(json.dumps, events)), "")
        with patch("coordinate.subprocess.run", return_value=result) as command:
            actual = coordinate.execute_worker("codex", {"title": "A", "task": "Inspect", "paths": []},
                                               (Path.cwd(), None), "Inspect this workspace", 5, False)
        args = command.call_args.args[0]
        self.assertIn("read-only", args)
        self.assertNotIn("--ephemeral", args)
        self.assertEqual(actual["status"], "completed")
        self.assertEqual(actual["thread_id"], "thread-worker")
        self.assertEqual(actual["answer"], "result")


if __name__ == "__main__":
    unittest.main()
