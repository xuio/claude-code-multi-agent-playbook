"""Tests for agent_scan.py on a synthetic session. Run: python3 -m unittest discover -s tools/tests"""
import datetime
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


if __name__ == "__main__":
    unittest.main()
