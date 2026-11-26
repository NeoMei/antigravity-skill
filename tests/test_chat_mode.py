import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid

SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "antigravity" / "scripts" / "chat_mode.py"


class ChatModeTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="antigravity mode ")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.home = self.root / "codex home"
        self.home.mkdir()
        self.bridge = self.root / "bridge program.py"
        self.log = self.root / "bridge-log.json"
        self.bridge.write_text(
            "import json, os, sys\n"
            "from pathlib import Path\n"
            "if sys.argv[1] == 'send':\n"
            "    Path(os.environ['BRIDGE_LOG']).write_text("
            "json.dumps({'thread': os.environ['CODEX_THREAD_ID'], 'body': sys.stdin.read()}))\n"
            "    print('ticket-1')\n"
            "elif sys.argv[1:] == ['receive', 'ticket-1']:\n"
            "    sys.stdout.write('原始回复\\n\\n    with spacing')\n"
            "else: raise SystemExit(2)\n",
            encoding="utf-8",
        )
        self.env = {**os.environ, "CODEX_HOME": str(self.home), "CODEX_THREAD_ID": str(uuid.uuid4()),
                    "BRIDGE_LOG": str(self.log)}

    def call(self, *args, env=None, body=None, check=True):
        result = subprocess.run(
            [sys.executable, "-B", str(SCRIPT), "--bridge", str(self.bridge), *args],
            cwd=self.root, env=env or self.env, text=True, input=body, capture_output=True, timeout=15,
        )
        if check:
            self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def test_bind_and_relay_are_scoped_to_the_current_chat(self):
        other = {**self.env, "CODEX_THREAD_ID": str(uuid.uuid4())}
        self.assertEqual(self.call("status").stdout.strip(), "codex")
        self.assertNotEqual(self.call("send", body="unbound", check=False).returncode, 0)
        self.assertFalse(self.log.exists())
        self.call("enable")
        self.assertEqual(self.call("status").stdout.strip(), "antigravity")
        self.assertEqual(self.call("status", env=other).stdout.strip(), "codex")
        self.assertNotEqual(self.call("send", env=other, body="wrong chat", check=False).returncode, 0)
        message = "第一段  \n\n保留 '引号' 和 $ 符号"
        self.assertEqual(self.call("send", body=message).stdout.strip(), "ticket-1")
        self.assertEqual(json.loads(self.log.read_text()),
                         {"thread": self.env["CODEX_THREAD_ID"], "body": message})
        self.assertEqual(self.call("receive", "ticket-1").stdout,
                         "Antigravity：\n\n原始回复\n\n    with spacing")
        self.call("disable")
        self.assertEqual(self.call("status").stdout.strip(), "codex")

    def test_setup_preserves_effective_user_instructions_and_is_idempotent(self):
        normal = self.home / "AGENTS.md"
        normal.write_text("# User defaults\n")
        override = self.home / "AGENTS.override.md"
        override.write_text("# Current user instructions\n")
        self.call("setup")
        first = override.read_text()
        self.assertTrue(first.startswith("# Current user instructions\n"))
        self.assertIn("启用 Antigravity", first)
        self.assertIn("退出 Antigravity", first)
        self.call("setup")
        self.assertEqual(override.read_text(), first)
        self.assertEqual(normal.read_text(), "# User defaults\n")


if __name__ == "__main__":
    unittest.main()
