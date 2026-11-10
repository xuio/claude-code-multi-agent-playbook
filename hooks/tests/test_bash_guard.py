"""Unit tests for bash_guard (run: python3 -m unittest discover -s hooks/tests)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import bash_guard as bg  # noqa: E402

SCRIPT = os.path.join(os.path.dirname(HERE), "bash_guard.py")
CFG = {"block_cd": True, "cd_message": bg.CD_MESSAGE, "blocked_commands": {
    "cargo": {"allow": {"check", "clippy", "fmt", "--version"}, "message": "Use the job queue."}}}


class TestCheck(unittest.TestCase):
    def blocked(self, cmd, cfg=CFG):
        return bg.check(cmd, cfg)

    def test_cd_and_pushd_at_command_positions(self):
        for cmd in ["cd /tmp", "cd /tmp && ls", "ls; cd /tmp", "ls && cd x", "true || pushd x", "(cd x && make)",
                    "ls |\ncd x", "FOO=1 cd x", "env FOO=1 cd x", "builtin cd x", "command cd x", "ls\ncd x",
                    "/usr/bin/cd x"]:
            self.assertIn("is not allowed in Bash calls", self.blocked(cmd) or "", cmd)

    def test_cd_as_text_passes(self):
        for cmd in ["echo cd", "git -C /repo status", "grep -n 'cd /tmp' notes.md", 'echo "run: cd x && make"',
                    "ls abcd", "queue run 'cd build && make'", "cat <<'EOF'\ncd /tmp\nEOF", "ls # cd x",
                    "mycd x", "git commit -m 'cd is gone'"]:
            self.assertIsNone(self.blocked(cmd), cmd)

    def test_blocked_commands(self):
        for cmd in ["cargo test", "cargo build --release", "cargo", "cargo +nightly test", "ls && cargo run",
                    "RUSTFLAGS=x cargo bench", "time cargo test", "~/.cargo/bin/cargo test"]:
            reason = self.blocked(cmd)
            self.assertIn("is not allowed here", reason or "", cmd)
            self.assertIn("Use the job queue.", reason)
        for cmd in ["cargo check", "cargo clippy -- -D warnings", "cargo +nightly fmt", "cargo --version",
                    'queue run "cargo test"', "echo cargo test", "ls | grep cargo"]:
            self.assertIsNone(self.blocked(cmd), cmd)

    def test_block_cd_off_and_no_config(self):
        self.assertIsNone(self.blocked("cd /tmp", dict(CFG, block_cd=False)))
        default = bg.load_config("/nonexistent/bash_guard.json")
        self.assertIsNotNone(bg.check("cd /tmp", default))
        self.assertIsNone(bg.check("cargo test", default))

    def test_parse_problem_fails_open(self):
        with self.assertRaises(ValueError):
            bg.check("echo 'unterminated", CFG)        # main() turns this into "allow"


class TestCli(unittest.TestCase):
    def run_cli(self, stdin, *argv):
        return subprocess.run([sys.executable, SCRIPT, *argv], input=stdin, capture_output=True, text=True, timeout=10)

    def call(self, command, *argv):
        return self.run_cli(json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash",
                                        "tool_input": {"command": command}}), *argv)

    def test_block_is_exit_2_with_reason_on_stderr(self):
        r = self.call("cd /tmp && ls")
        self.assertEqual((r.returncode, r.stdout), (2, ""))
        self.assertIn("Blocked: `cd` is not allowed in Bash calls (use absolute paths and `git -C <dir>`).", r.stderr)

    def test_allow_and_fail_open(self):
        for stdin in [json.dumps({"tool_input": {"command": "ls -la"}}), "", "not json", "[]",
                      json.dumps({"tool_input": {"command": "echo 'unterminated"}}), json.dumps({"tool_input": None})]:
            r = self.run_cli(stdin)
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""), stdin)

    def test_config_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "bash_guard.json")
            with open(p, "w") as f:
                json.dump({"block_cd": False, "blocked_commands": {
                    "xcodebuild": {"allow_subcommands": ["-version"], "message": "Builds run on the build host."}}}, f)
            self.assertEqual(self.call("cd /tmp", "--config", p).returncode, 0)
            self.assertEqual(self.call("xcodebuild -version", "--config", p).returncode, 0)
            r = self.call("xcodebuild -scheme App build", "--config", p)
            self.assertEqual(r.returncode, 2)
            self.assertIn("Builds run on the build host.", r.stderr)
            with open(p, "w") as f:
                f.write("{broken")
            self.assertEqual(self.call("xcodebuild build", "--config", p).returncode, 0)   # bad config: cd only
            self.assertEqual(self.call("cd /tmp", "--config", p).returncode, 2)


if __name__ == "__main__":
    unittest.main()
