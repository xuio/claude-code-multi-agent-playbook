"""Tests for the plugin packaging: hooks/hooks.json, hooks/plugin-run.sh and bin/context-guard-ctl.

Run: python3 -m unittest discover -s hooks/tests
"""
import json
import os
import re
import subprocess
import tempfile
import unittest

HOOKS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
ROOT = os.path.join(HOOKS, "..")
RUN = os.path.join(HOOKS, "plugin-run.sh")

WAIT = {"hook_event_name": "PreToolUse", "session_id": "s", "agent_id": "a1", "agent_type": "lane",
        "tool_name": "Bash", "tool_use_id": "t1", "tool_input": {"command": "sleep 1", "timeout": 400000}}
CD = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "cd /tmp"}}


class PluginRunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.plain = os.path.join(self.tmp.name, "plain")
        self.project = os.path.join(self.tmp.name, "project")
        self.hooks = os.path.join(self.project, ".claude", "hooks")
        self.worktree = os.path.join(self.project, ".claude", "worktrees", "w1")
        for d in (self.plain, self.hooks, self.worktree):
            os.makedirs(d)
        with open(os.path.join(self.hooks, "context_guard.json"), "w") as f:
            json.dump({"state_dir": os.path.join(self.tmp.name, "state"), "log": ""}, f)
        with open(os.path.join(self.hooks, "bash_guard.json"), "w") as f:
            json.dump({"block_cd": True}, f)
        with open(os.path.join(self.hooks, "after-compaction.txt"), "w") as f:
            f.write("read your rules file\n")

    def run_hook(self, project, mode, inp=None):
        # HOME is the temp folder: the compaction log's default path must not be the real one.
        env = dict(os.environ, CLAUDE_PROJECT_DIR=project, CLAUDE_PLUGIN_ROOT=ROOT, HOME=self.tmp.name)
        env.pop("CONTEXT_GUARD_CONFIG", None)
        env.pop("BASH_GUARD_CONFIG", None)
        r = subprocess.run(["sh", RUN, mode], input=json.dumps(inp or {}), capture_output=True, text=True,
                           env=env, cwd=self.tmp.name, timeout=20)
        return r.returncode, r.stdout, r.stderr

    def test_a_project_without_config_files_is_left_alone(self):
        for mode, inp in (("guard", WAIT), ("bash-guard", CD), ("after-compaction", None), ("compaction-log", {})):
            self.assertEqual(self.run_hook(self.plain, mode, inp), (0, "", ""), mode)

    def test_the_guard_runs_with_the_projects_config(self):
        code, out, _ = self.run_hook(self.project, "guard", WAIT)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_the_bash_guard_blocks_with_exit_2_and_passes_the_rest(self):
        code, out, err = self.run_hook(self.project, "bash-guard", CD)
        self.assertEqual((code, out), (2, ""))
        self.assertIn("cd", err)
        ok = dict(CD, tool_input={"command": "ls /tmp"})
        self.assertEqual(self.run_hook(self.project, "bash-guard", ok)[0], 0)

    def test_the_reminder_is_printed(self):
        self.assertEqual(self.run_hook(self.project, "after-compaction"), (0, "read your rules file\n", ""))

    def test_a_session_in_a_worktree_below_the_project_finds_the_projects_files(self):
        code, out, _ = self.run_hook(self.worktree, "guard", WAIT)
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(self.run_hook(self.worktree, "after-compaction")[1], "read your rules file\n")

    def settings(self, folder, name, hooks=None, allow=None):
        os.makedirs(os.path.join(folder, ".claude"), exist_ok=True)
        with open(os.path.join(folder, ".claude", name), "w") as f:
            json.dump({"permissions": {"allow": allow or []}, "hooks": hooks or {}}, f, indent=2)

    def entry(self, command):
        return [{"matcher": "*", "hooks": [{"type": "command", "command": command}]}]

    def test_a_hook_the_project_registers_itself_is_left_to_the_project(self):
        own = 'python3 -I "%s/context_guard.py" 2>/dev/null || true' % self.hooks
        self.settings(self.project, "settings.local.json", {
            "PostToolUse": self.entry(own),
            "SessionStart": self.entry('cat "%s/after-compaction.txt"' % self.hooks)})
        self.assertEqual(self.run_hook(self.project, "guard", WAIT), (0, "", ""))
        self.assertEqual(self.run_hook(self.project, "after-compaction"), (0, "", ""))
        self.assertEqual(self.run_hook(self.worktree, "guard", WAIT), (0, "", ""))   # found from below too
        self.assertEqual(self.run_hook(self.project, "bash-guard", CD)[0], 2)        # not registered: the plugin's
        # Once the entries are gone, the plugin takes over at the next call.
        self.settings(self.project, "settings.local.json")
        self.assertIn("deny", self.run_hook(self.project, "guard", WAIT)[1])
        self.assertEqual(self.run_hook(self.project, "after-compaction")[1], "read your rules file\n")

    def test_a_worktrees_own_settings_count_and_a_permission_rule_does_not(self):
        self.settings(self.worktree, "settings.local.json",
                      {"PreToolUse": self.entry('python3 "%s/context_guard.py"' % self.hooks)})
        self.assertEqual(self.run_hook(self.worktree, "guard", WAIT), (0, "", ""))
        self.assertIn("deny", self.run_hook(self.project, "guard", WAIT)[1])         # the project itself: active
        self.settings(self.project, "settings.json",
                      allow=["Bash(python3 .claude/hooks/context_guard.py:*)", "Read(.claude/hooks/after-compaction.txt)"])
        self.assertIn("deny", self.run_hook(self.project, "guard", WAIT)[1])
        self.assertEqual(self.run_hook(self.project, "after-compaction")[1], "read your rules file\n")

    def test_the_compaction_log_prints_nothing(self):
        # Claude Code takes a PreCompact hook's stdout as compaction instructions.
        code, out, _ = self.run_hook(self.project, "compaction-log",
                                     {"hook_event_name": "PreCompact", "trigger": "auto", "session_id": "s1"})
        self.assertEqual((code, out), (0, ""))
        with open(os.path.join(self.tmp.name, ".cache", "context-guard", "compactions.jsonl")) as f:
            lines = [json.loads(line) for line in f]
        self.assertEqual([(d["session_id"], d["trigger"]) for d in lines], [("s1", "auto")])

    def test_an_unknown_mode_and_a_broken_config_block_nothing(self):
        self.assertEqual(self.run_hook(self.project, "no-such-mode")[0], 0)
        with open(os.path.join(self.hooks, "context_guard.json"), "w") as f:
            f.write("not json")
        self.assertEqual(self.run_hook(self.project, "guard", dict(WAIT, tool_input={"command": "ls"}))[0], 0)

    def test_hooks_json_names_only_modes_the_wrapper_knows(self):
        with open(os.path.join(HOOKS, "hooks.json")) as f:
            registered = json.load(f)["hooks"]
        with open(RUN) as f:
            known = set(re.findall(r"^  ([a-z-]+)\)$", f.read(), re.M))
        used = set()
        for groups in registered.values():
            for group in groups:
                for hook in group["hooks"]:
                    self.assertIn('"${CLAUDE_PLUGIN_ROOT}/hooks/plugin-run.sh"', hook["command"])
                    used.add(hook["command"].split()[-1])
        self.assertEqual(used, known)
        self.assertEqual(set(registered), {"PreToolUse", "PostToolUse", "SessionStart", "PreCompact"})

    def test_the_control_command_runs_under_its_stable_name(self):
        ctl = os.path.join(ROOT, "bin", "context-guard-ctl")
        self.assertTrue(os.access(ctl, os.X_OK))
        r = subprocess.run([ctl, "--config", os.path.join(self.hooks, "context_guard.json"), "show"],
                           capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0)
        self.assertIn("limit 150k", r.stdout)


if __name__ == "__main__":
    unittest.main()
