"""Tests for agent_scan.py on a synthetic session. Run: python3 -m unittest discover -s tools/tests"""
import contextlib
import datetime
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import agent_scan  # noqa: E402

NOW = datetime.datetime(2030, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)


def stamp(minutes_ago):
    return (NOW - datetime.timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")


def local(minutes_ago):
    """That time as the tool prints it: local date and time."""
    return (NOW - datetime.timedelta(minutes=minutes_ago)).astimezone().strftime("%Y-%m-%d %H:%M")


def call(minutes_ago, ctx, tool=None, command="", mid=None, written=0, result=True, stop=None):
    """One model call (and the tool result that answers it) as transcript lines."""
    content = []
    if tool:
        content.append({"type": "tool_use", "id": "t%s" % minutes_ago, "name": tool, "input": {"command": command}})
    lines = [{"type": "assistant", "timestamp": stamp(minutes_ago),
              "message": {"id": mid or "m%s" % minutes_ago, "model": "some-model", "stop_reason": stop,
                          "usage": {"input_tokens": 10, "cache_read_input_tokens": ctx - 10 - written,
                                    "cache_creation_input_tokens": written},
                          "content": content}}]
    if tool and result:
        lines.append({"type": "user", "timestamp": stamp(minutes_ago),
                      "message": {"content": [{"type": "tool_result", "tool_use_id": "t%s" % minutes_ago}]}})
    return lines


class Session:
    def __init__(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "session.jsonl")
        self.write(self.path, call(200, 50_000, "Bash", "ls") + call(1, 60_000, "Bash", "ls"))

    def write(self, path, lines):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            for line in lines:
                f.write(json.dumps(line) + "\n")

    def agent(self, aid, lines, kind="worker", finished=None):
        base = os.path.join(self.dir.name, "session", "subagents", "agent-%s" % aid)
        self.write(base + ".jsonl", lines)
        meta = {"agentType": kind, "description": "lane " + aid}
        if finished is not None:
            meta["finished"] = finished
        with open(base + ".meta.json", "w") as f:
            json.dump(meta, f)

    def rows(self, show_all=True, cfg=None):
        out = agent_scan.rows(self.path, cfg or agent_scan.load_limits(""), NOW, show_all)
        return {r["name"].split()[0]: r for r in out}


class AgentScanTest(unittest.TestCase):
    def session(self):
        s = Session()
        self.addCleanup(s.dir.cleanup)
        return s

    def test_states(self):
        s = self.session()
        s.agent("run", call(30, 40_000, "Bash", "ls") + call(1, 50_000, "Bash", "ls"))
        s.agent("done", call(30, 40_000, "Bash", "ls") + call(20, 50_000, stop="end_turn"))
        s.agent("marked", call(30, 40_000, "Bash", "ls"), finished=True)
        s.agent("old", call(400, 40_000, "Bash", "ls") + call(300, 50_000, "Bash", "ls"))
        rows = s.rows()
        self.assertEqual({k: r["state"] for k, r in rows.items()},
                         {"coordinator": "running", "run": "running", "done": "ended", "marked": "ended",
                          "old": "stale"})
        self.assertEqual(sorted(s.rows(show_all=False)), ["coordinator", "run"])

    def test_context_flags_follow_the_limits(self):
        s = self.session()
        s.agent("low", call(5, 80_000, "Bash", "ls") + call(1, 125_000, "Bash", "ls"))
        s.agent("wrap", call(5, 80_000, "Bash", "ls") + call(1, 135_000, "Bash", "ls"))
        s.agent("over", call(5, 80_000, "Bash", "ls") + call(1, 155_000, "Bash", "ls"))
        s.agent("small", call(5, 70_000, "Bash", "ls") + call(1, 85_000, "Bash", "ls"), kind="batch-worker")
        cfg = agent_scan.load_limits("")
        cfg["agent_types"] = {"batch-worker": {"limit": 80_000}}
        rows = s.rows(cfg=cfg)
        self.assertEqual(rows["low"]["flags"], [])
        self.assertEqual(rows["wrap"]["flags"], ["CTX"])
        self.assertEqual(rows["over"]["flags"], ["OVER"])
        self.assertEqual(rows["small"]["flags"], ["OVER"])
        self.assertEqual(rows["small"]["limit"], 80_000)

    def test_overrides_written_by_the_control_command_are_used(self):
        s = self.session()
        s.agent("raised", call(5, 80_000, "Bash", "ls") + call(1, 125_000, "Bash", "ls"))
        s.agent("typed", call(5, 80_000, "Bash", "ls") + call(1, 125_000, "Bash", "ls"), kind="batch-worker")
        s.agent("other", call(5, 80_000, "Bash", "ls") + call(1, 125_000, "Bash", "ls"))
        config = os.path.join(s.dir.name, "context_guard.json")
        with open(config, "w") as f:
            json.dump({"lane_limit": 100_000, "agent_types": {"batch-worker": {"limit": 80_000}}}, f)
        with open(os.path.join(s.dir.name, "context_guard.overrides.json"), "w") as f:
            json.dump({"agents": {"raised": {"limit": 200_000, "until": "2030-01-01T13:00:00+00:00"},
                                  "other": {"limit": 300_000, "until": "2030-01-01T11:00:00+00:00"}},
                       "types": {"batch-worker": {"limit": 150_000, "wrapup": 140_000}},
                       "coordinator": {"compact_at": 50_000}}, f)
        rows = s.rows(cfg=agent_scan.load_limits(config, NOW))
        self.assertEqual((rows["raised"]["limit"], rows["raised"]["flags"]), (200_000, []))
        self.assertEqual((rows["typed"]["limit"], rows["typed"]["flags"]), (150_000, []))
        self.assertEqual((rows["other"]["limit"], rows["other"]["flags"]), (100_000, ["OVER"]))   # expired
        self.assertEqual((rows["coordinator"]["limit"], rows["coordinator"]["flags"]), (50_000, ["OVER"]))

    def test_zero_means_no_limit_and_a_bad_config_means_defaults(self):
        s = self.session()
        s.agent("free", call(5, 80_000, "Bash", "ls") + call(1, 500_000, "Bash", "ls"), kind="reader")
        s.agent("tight", call(5, 80_000, "Bash", "ls") + call(1, 140_000, "Bash", "ls"), kind="exact")
        config = os.path.join(s.dir.name, "context_guard.json")
        with open(config, "w") as f:
            json.dump({"agent_types": {"reader": {"limit": 0}, "exact": {"wrapup_margin": 0}}}, f)
        with open(os.path.join(s.dir.name, "context_guard.overrides.json"), "w") as f:
            json.dump({"coordinator": {"compact_at": 0}}, f)
        rows = s.rows(cfg=agent_scan.load_limits(config, NOW))
        self.assertEqual(rows["free"]["flags"], [])
        self.assertEqual(rows["tight"]["flags"], [])              # margin 0: no wrap-up point before 150k
        self.assertEqual(rows["coordinator"]["flags"], [])
        for bad in ("[1, 2]", "not json", '"text"'):
            with open(config, "w") as f:
                f.write(bad)
            with open(os.path.join(s.dir.name, "context_guard.overrides.json"), "w") as f:
                f.write(bad)
            cfg = agent_scan.load_limits(config, NOW)
            self.assertEqual((cfg["lane_limit"], cfg["overrides"]["agents"]), (150_000, {}))

    def test_silent_hung_and_polling(self):
        s = self.session()
        s.agent("quiet", call(40, 40_000, "Bash", "ls") + call(20, 41_000, "Bash", "ls"))
        s.agent("hung", call(40, 40_000, "Bash", "ls") + call(25, 41_000, "Bash", "git commit", result=False))
        polls = []
        for m in range(10, 0, -1):
            polls += call(m, 40_000 + m, "Bash", "sleep 200; tail -3 run.log")
        s.agent("poll", polls)
        rows = s.rows()
        self.assertEqual(rows["quiet"]["flags"], ["SILENT"])
        self.assertEqual(rows["hung"]["flags"], ["SILENT", "HUNG"])
        self.assertEqual(rows["poll"]["flags"], ["POLLING"])

    def test_nomsg_needs_an_hour_without_a_message(self):
        s = self.session()
        s.agent("mute", call(90, 40_000, "Bash", "ls") + call(1, 50_000, "Bash", "ls"))
        s.agent("talks", call(90, 40_000, "Bash", "ls") + call(20, 45_000, "SendMessage") + call(1, 50_000, "Bash", "ls"))
        s.agent("late", call(150, 40_000, "SendMessage") + call(1, 50_000, "Bash", "ls"))
        s.agent("young", call(30, 40_000, "Bash", "ls") + call(1, 50_000, "Bash", "ls"))
        rows = s.rows()
        self.assertEqual(rows["mute"]["flags"], ["NOMSG"])
        self.assertEqual(rows["talks"]["flags"], [])
        self.assertEqual(rows["late"]["flags"], ["NOMSG"])
        self.assertEqual(rows["young"]["flags"], [])
        self.assertNotIn("NOMSG", rows["coordinator"]["flags"])

    def test_cache_rewrites_are_counted(self):
        s = self.session()
        lines = call(50, 60_000, "Bash", "ls")
        for m in (40, 30, 20):
            lines += call(m, 60_000 + m, "Bash", "ls", written=50_000)
        lines += call(1, 61_000, "Bash", "ls", written=500)
        s.agent("woken", lines)
        row = s.rows()["woken"]
        self.assertEqual(row["rewrites"], 3)
        self.assertEqual(row["rewrite_tokens"], 150_000)
        self.assertEqual(row["flags"], ["REWRITES"])
        self.assertEqual(row["peak"], 61_000)

    def test_a_message_split_over_lines_counts_once(self):
        s = self.session()
        s.agent("split", call(5, 50_000, "Bash", "ls", mid="same") + call(5, 50_000, "Read", mid="same")
                + call(1, 51_000, "Bash", "ls"))
        self.assertEqual(s.rows()["split"]["model_calls"], 2)

    def test_broken_lines_and_missing_meta_do_not_stop_the_scan(self):
        s = self.session()
        base = os.path.join(s.dir.name, "session", "subagents", "agent-bare.jsonl")
        os.makedirs(os.path.dirname(base), exist_ok=True)
        with open(base, "w") as f:
            f.write('{"timestamp": broken\n')
            for line in call(1, 30_000, "Bash", "ls"):
                f.write(json.dumps(line) + "\n")
        row = s.rows()["bare"]
        self.assertEqual((row["type"], row["state"], row["ctx"]), ("?", "running", 30_000))


def text(minutes_ago, words, mid=None):
    """An assistant line that only says something."""
    return [{"type": "assistant", "timestamp": stamp(minutes_ago),
             "message": {"id": mid or "x%s" % minutes_ago, "model": "some-model", "stop_reason": "end_turn",
                         "content": [{"type": "text", "text": words}]}}]


class AgentDetailTest(unittest.TestCase):
    def setUp(self):
        self.s = Session()
        self.addCleanup(self.s.dir.cleanup)
        self.s.agent("a1b2c3d4e5", call(90, 40_000, "Bash", "ls") + call(80, 45_000, "Read")
                     + text(70, "Done.\nThe parser is ported " + "x" * 400), kind="engineer")
        meta = os.path.join(self.s.dir.name, "session", "subagents", "agent-a1b2c3d4e5.meta.json")
        with open(meta, "w") as f:
            json.dump({"agentType": "engineer", "description": "Port the parser"}, f)
        self.s.agent("a1b2ffff00", call(10, 30_000, "Bash", "ls"), kind="reader")

    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = agent_scan.main(["--session", self.s.path, "--now", NOW.isoformat(), *argv])
        return code, out.getvalue(), err.getvalue()

    def test_block_fields(self):
        code, out, _ = self.run_main("--agent", "a1b2c3")
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertEqual(lines[0], "agent a1b2c3d4e5  engineer  Port the parser")
        self.assertIn("state      ended", out)
        self.assertIn("context    45k of 150k (peak 45k)", out)
        first = (NOW - datetime.timedelta(minutes=90)).astimezone().strftime("%Y-%m-%d %H:%M")
        last = (NOW - datetime.timedelta(minutes=70)).astimezone().strftime("%Y-%m-%d %H:%M")
        self.assertIn("first      " + first, out)
        self.assertIn("last       %s (70.0 min ago)" % last, out)
        self.assertIn("tool calls 2", out)
        tail = [ln for ln in lines if ln.startswith("  last text")][0]
        self.assertTrue(tail.startswith("  last text  Done. The parser is ported xx"))
        self.assertEqual(len(tail) - len("  last text  "), 300)
        self.assertNotIn("since", out)

    def test_since_without_activity(self):
        for when in ("60m", "1h", (NOW - datetime.timedelta(minutes=60)).isoformat()):
            code, out, _ = self.run_main("--agent", "a1b2c3", "--since", when)
            self.assertEqual(code, 0)
            self.assertEqual(out.splitlines()[-1], "  since %s: no activity" % local(60))

    def test_since_with_activity(self):
        hhmm = (NOW - datetime.timedelta(minutes=85)).astimezone().strftime("%H:%M")
        code, out, _ = self.run_main("--agent", "a1b2c3", "--since", hhmm)
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines()[-1],
                         "  since %s: 2 assistant messages, 1 tool calls, last at %s" % (local(85), local(70)))

    def test_hhmm_read_as_yesterday_shows_its_date(self):
        later = (NOW + datetime.timedelta(minutes=5)).astimezone().strftime("%H:%M")
        code, out, _ = self.run_main("--agent", "a1b2c3", "--since", later)
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines()[-1],
                         "  since %s: 3 assistant messages, 2 tool calls, last at %s"
                         % (local(24 * 60 - 5), local(70)))

    def test_a_timestamp_without_an_offset_is_utc(self):
        naive = (NOW - datetime.timedelta(minutes=30)).replace(tzinfo=None).isoformat()
        self.assertEqual(agent_scan.parse_ts(naive), NOW - datetime.timedelta(minutes=30))
        self.assertIsNone(agent_scan.parse_ts("not a time"))
        lines = call(30, 20_000, "Bash", "ls")
        for line in lines:
            line["timestamp"] = naive
        self.s.agent("c0ffee0000", lines)
        code, out, _ = self.run_main("--agent", "c0ffee", "--since", "1h")
        self.assertEqual(code, 0)
        self.assertIn("last       %s (30.0 min ago)" % local(30), out)
        self.assertEqual(out.splitlines()[-1],
                         "  since %s: 1 assistant messages, 1 tool calls, last at %s" % (local(60), local(30)))

    def test_hhmm_in_the_future_means_yesterday(self):
        later = (NOW + datetime.timedelta(minutes=5)).astimezone().strftime("%H:%M")
        t = agent_scan.parse_since(later, NOW)
        self.assertTrue(datetime.timedelta(hours=23) < NOW - t <= datetime.timedelta(hours=24))
        self.assertIsNone(agent_scan.parse_since("soon", NOW))

    def test_json(self):
        code, out, _ = self.run_main("--agent", "a1b2c3d4e5", "--since", "2h", "--json")
        self.assertEqual(code, 0)
        d = json.loads(out)
        self.assertEqual((d["id"], d["type"], d["description"], d["state"], d["ctx"], d["limit"], d["tool_calls"]),
                         ("a1b2c3d4e5", "engineer", "Port the parser", "ended", 45_000, 150_000, 2))
        self.assertEqual(d["idle_min"], 70.0)
        self.assertEqual((d["since"]["when"], d["since"]["assistant_messages"], d["since"]["tool_calls"]),
                         ("2h", 3, 2))
        self.assertEqual((d["since"]["from"], d["since"]["last"]), (local(120), local(70)))

    def test_ambiguous_prefix_lists_the_matches(self):
        code, out, err = self.run_main("--agent", "a1b2")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("2 sub-agents match a1b2", err)
        self.assertIn("a1b2c3d4e5  engineer  Port the parser", err)
        self.assertIn("a1b2ffff00  reader", err)

    def test_unknown_id(self):
        code, out, err = self.run_main("--agent", "ffff")
        self.assertEqual((code, out), (1, ""))
        self.assertEqual(len(err.strip().splitlines()), 1)
        self.assertIn("no sub-agent with id ffff", err)

    def test_since_needs_agent(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            agent_scan.main(["--session", self.s.path, "--since", "1h"])


if __name__ == "__main__":
    unittest.main()
