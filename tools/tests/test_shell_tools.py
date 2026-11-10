"""Tests for tools/sweep_timer.sh and tools/orient.sh.

Run: python3 -m unittest discover -s tools/tests
"""
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

TOOLS = pathlib.Path(__file__).resolve().parent.parent
SWEEP = TOOLS / "sweep_timer.sh"
ORIENT = TOOLS / "orient.sh"


def run(args, env=None, cwd=None):
    return subprocess.run(args, capture_output=True, text=True, env=env, cwd=cwd, timeout=60)


class SweepTimerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="sweep timer "))
        self.addCleanup(shutil.rmtree, self.tmp)
        # The script itself lives in a folder with a space, so its own path needs quoting too.
        self.script_dir = self.tmp / "tool dir"
        self.script_dir.mkdir()
        self.script = self.script_dir / "sweep_timer.sh"
        shutil.copy(SWEEP, self.script)
        self.cmd = self.tmp / "my cmd.sh"
        self.cmd.write_text('#!/bin/sh\nfor a in "$@"; do echo "arg=[$a]"; done\n'
                            'echo run >> "$(dirname "$0")/runs.log"\nexit "${EXIT_CODE:-0}"\n')
        self.cmd.chmod(0o755)

    def test_rearm_is_last_line_and_reruns_the_same_command(self):
        args = ["bash", str(self.script), "0", str(self.cmd), "two words", "it's", "$HOME", "*"]
        first = run(args, cwd=str(self.tmp))
        self.assertEqual(first.returncode, 0, first.stderr)
        lines = first.stdout.splitlines()
        self.assertTrue(lines[-1].startswith("RE-ARM: "), lines)
        self.assertEqual(lines[:-1], ["arg=[two words]", "arg=[it's]", "arg=[$HOME]", "arg=[*]", ""])
        rearm = lines[-1][len("RE-ARM: "):]
        self.assertIn(str(self.script_dir).replace(" ", "\\ "), rearm)
        # Paste the line into a shell from another folder: same output, same RE-ARM line.
        second = run(["bash", "-c", rearm], cwd="/")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(second.stdout, first.stdout)
        self.assertEqual((self.tmp / "runs.log").read_text(), "run\nrun\n")

    def test_rearm_starts_its_own_line_after_output_without_a_newline(self):
        res = run(["bash", str(self.script), "0", "printf", "no-newline"])
        self.assertEqual(res.returncode, 0, res.stderr)
        lines = res.stdout.splitlines()
        self.assertEqual(lines[0], "no-newline")
        self.assertTrue(lines[-1].startswith("RE-ARM: "), lines)
        self.assertTrue(res.stdout.endswith(" printf no-newline\n"), res.stdout)

    def test_exit_status_is_the_commands(self):
        env = dict(os.environ, EXIT_CODE="3")
        res = run(["bash", str(self.script), "0", str(self.cmd)], env=env)
        self.assertEqual(res.returncode, 3)
        self.assertTrue(res.stdout.splitlines()[-1].startswith("RE-ARM: "))

    def test_minutes_with_a_leading_zero_are_decimal(self):
        # A stand-in for sleep, first in PATH, that records how long it was asked to wait.
        fake = self.tmp / "bin"
        fake.mkdir()
        (fake / "sleep").write_text('#!/bin/sh\necho "$1" > "$(dirname "$0")/slept"\n')
        (fake / "sleep").chmod(0o755)
        env = dict(os.environ, PATH="%s%s%s" % (fake, os.pathsep, os.environ["PATH"]))
        res = run(["bash", str(self.script), "08", str(self.cmd)], env=env)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual((fake / "slept").read_text().strip(), "480")
        self.assertTrue(res.stdout.splitlines()[-1].startswith("RE-ARM: "))

    def test_bad_minutes_is_refused(self):
        res = run(["bash", str(self.script), "1.5", str(self.cmd)])
        self.assertEqual(res.returncode, 2)
        self.assertFalse((self.tmp / "runs.log").exists())


class OrientTest(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="orient root "))
        self.addCleanup(shutil.rmtree, self.root)
        (self.root / "ops").mkdir()
        (self.root / "notes").mkdir()
        self.env = dict(os.environ, ORIENT_ROOT=str(self.root))

    def write(self, rel, text):
        (self.root / rel).write_text(text)

    def orient(self, **extra):
        env = dict(self.env, **extra)
        res = run(["bash", str(ORIENT)], env=env, cwd=str(self.root))
        self.assertEqual(res.returncode, 0, res.stderr)
        return res.stdout

    def full_setup(self):
        self.write("ops/rules-digest.md",
                   "# Rules digest (covers through hints line 3, 11-10)\n- rule one (H2)\n")
        self.write("ops/hints.md", "".join(f"FACT line {i}\n" for i in range(1, 7)))
        self.write("notes/resume.md", "".join(f"STATE {i}\n" for i in range(1, 51)))
        self.write("ops/lanes.md",
                   "# Lane ledger\n\n"
                   "| lane | agent id | state | opened | closed |\n"
                   "|---|---|---|---|---|\n"
                   "| alpha | a1 | live | 11-10 09:00 | |\n"
                   "| beta | b2 | closed | 11-10 08:00 | 11-10 09:30 |\n"
                   "| gamma | c3 | live | 11-10 10:00 | |\n")
        self.write("ops/duties.log", "".join(f"duty {i}\n" for i in range(1, 21)))

    def test_hints_after_header_live_rows_and_tails(self):
        self.full_setup()
        out = self.orient()
        self.assertIn("4: FACT line 4\n5: FACT line 5\n6: FACT line 6\n", out)
        self.assertNotIn("FACT line 3", out)
        self.assertIn("STATE 40\n", out)
        self.assertNotIn("STATE 41", out)
        self.assertIn("| alpha | a1 | live", out)
        self.assertIn("| gamma | c3 | live", out)
        self.assertNotIn("beta", out)
        self.assertIn("duty 13\n", out)
        self.assertNotIn("duty 12\n", out)
        self.assertRegex(out, r"digest: \d+ B of 15000, longest line \d+ of 400: OK")

    def test_each_table_is_read_by_its_own_header(self):
        self.full_setup()
        self.write("ops/lanes.md",
                   "| lane | agent id | state |\n"
                   "|---|---|---|\n"
                   "| alpha | a1 | live |\n"
                   "\n"
                   "Closed lanes, another layout:\n"
                   "\n"
                   "| other | state | lane |\n"
                   "|---|---|---|\n"
                   "| q | closed | live |\n"
                   "| r | live | delta |\n"
                   "\n"
                   "| name | note |\n"
                   "|---|---|\n"
                   "| state | live |\n")
        out = self.orient()
        self.assertIn("| alpha | a1 | live |", out)
        self.assertIn("| r | live | delta |", out)
        self.assertNotIn("| q | closed | live |", out)
        self.assertNotIn("| state | live |", out)

    def test_ledger_without_a_state_column_and_without_live_rows(self):
        self.full_setup()
        self.write("ops/lanes.md", "| lane | note |\n|---|---|\n| alpha | live |\n")
        self.assertIn('(no table with a "state" column)', self.orient())
        self.write("ops/lanes.md", "| lane | state |\n|---|---|\n| alpha | closed |\n")
        self.assertIn("(no live lanes)", self.orient())

    def test_over_on_bytes_and_on_line_length(self):
        self.full_setup()
        out = self.orient(ORIENT_DIGEST_MAX_BYTES="20")
        self.assertRegex(out, r"digest: \d+ B of 20, longest line \d+ of 400: OVER")
        long_line = "x" * 401
        self.write("ops/rules-digest.md", f"# Rules digest (covers through hints line 3)\n{long_line}\n")
        out = self.orient()
        self.assertIn("longest line 401 of 400: OVER", out)

    def test_exact_cap_is_ok(self):
        self.full_setup()
        body = "# Rules digest (covers through hints line 3)\n"
        size = len(body.encode())
        self.write("ops/rules-digest.md", body)
        out = self.orient(ORIENT_DIGEST_MAX_BYTES=str(size), ORIENT_DIGEST_MAX_LINE=str(size - 1))
        self.assertIn(f"digest: {size} B of {size}, longest line {size - 1} of {size - 1}: OK", out)

    def test_missing_files_do_not_stop_the_report(self):
        out = self.orient()
        for rel in ("ops/rules-digest.md", "notes/resume.md", "ops/lanes.md", "ops/duties.log"):
            self.assertIn(f"(missing: {self.root / rel})", out)
        self.assertIn("== caps ==", out)

    def test_digest_without_header(self):
        self.full_setup()
        self.write("ops/rules-digest.md", "# Rules digest\n")
        out = self.orient()
        self.assertIn('(no "covers through line N" header', out)


if __name__ == "__main__":
    unittest.main()
