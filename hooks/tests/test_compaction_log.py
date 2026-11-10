"""Unit tests for compaction_log (run: python3 -m unittest discover -s hooks/tests)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "compaction_log.py")
SECRET = "do not log this text"


class TestCompactionLog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.tp = os.path.join(self.tmp, "sess.jsonl")
        with open(self.tp, "w") as f:
            f.write(json.dumps({"type": "user", "message": {"content": SECRET}}) + "\n")
            f.write(json.dumps({"type": "assistant", "message": {
                "model": "claude-test", "content": [{"type": "text", "text": SECRET}],
                "usage": {"input_tokens": 5, "cache_read_input_tokens": 165_000,
                          "cache_creation_input_tokens": 2_000}}}) + "\n")
        self.log = os.path.join(self.tmp, "logs", "compactions.jsonl")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_hook(self, stdin, *argv):
        return subprocess.run([sys.executable, SCRIPT, *argv], input=stdin, capture_output=True, text=True,
                              timeout=10)

    def lines(self):
        with open(self.log) as f:
            return [json.loads(ln) for ln in f]

    def test_one_line_per_compaction_without_content(self):
        for trigger, ci in (("auto", ""), ("manual", SECRET)):
            inp = {"hook_event_name": "PreCompact", "session_id": "s1", "transcript_path": self.tp,
                   "trigger": trigger, "custom_instructions": ci}
            r = self.run_hook(json.dumps(inp), "--log", self.log)
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
        recs = self.lines()
        self.assertEqual([(x["session_id"], x["trigger"], x["custom_instructions_len"], x["ctx"]) for x in recs],
                         [("s1", "auto", 0, 167_005), ("s1", "manual", len(SECRET), 167_005)])
        with open(self.log) as f:
            self.assertNotIn(SECRET, f.read())

    def test_missing_transcript_and_garbage_fail_open(self):
        r = self.run_hook(json.dumps({"session_id": "s2", "trigger": "auto", "transcript_path": "/nonexistent"}),
                          "--log", self.log)
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertIsNone(self.lines()[0]["ctx"])
        for stdin in ("", "not json", "[]"):
            r = self.run_hook(stdin, "--log", self.log)
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
        r = self.run_hook("{}", "--log", "/dev/null/x/log.jsonl")        # unwritable log
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
        self.assertEqual(len(self.lines()), 1)


if __name__ == "__main__":
    unittest.main()
