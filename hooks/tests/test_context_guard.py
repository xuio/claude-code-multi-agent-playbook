"""Unit tests for context_guard (run: python3 -m unittest discover -s hooks/tests)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import context_guard as cg  # noqa: E402

SCRIPT = os.path.join(os.path.dirname(HERE), "context_guard.py")
AID = "a1234567890abcdef"
NOW = 1_791_000_000.0
PROBE = "CONTEXT-GUARD-E2E-PROBE"
UNTIL_LATER = "2026-10-13T16:00:00+02:00"      # NOW is 2026-10-04


def assistant(prompt, tool_use_id=None, ts="2026-10-09T20:00:00.000Z", model="claude-test"):
    content = [{"type": "tool_use", "id": tool_use_id, "name": "Bash", "input": {}}] if tool_use_id else []
    return {"type": "assistant", "timestamp": ts, "message": {
        "model": model, "id": f"msg{prompt}", "content": content,
        "usage": {"input_tokens": 5, "cache_read_input_tokens": max(prompt - 5, 0),
                  "cache_creation_input_tokens": 0}}}


def write_lines(path, records):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.main_tp = os.path.join(self.tmp, "proj", "sess.jsonl")
        self.sub_tp = os.path.join(self.tmp, "proj", "sess", "subagents", f"agent-{AID}.jsonl")
        write_lines(self.main_tp, [
            {"type": "user", "timestamp": "2026-10-09T20:00:00.000Z", "message": {"content": "main brief"}},
            assistant(240_005, "toolu_main", ts="2026-10-09T20:01:00.000Z"),
            {"type": "assistant", "timestamp": "2026-10-09T20:02:00.000Z",
             "message": {"model": "<synthetic>", "content": [], "usage": {"input_tokens": 0}}},
        ])
        write_lines(self.sub_tp, [
            {"type": "user", "timestamp": "2026-10-09T20:00:00.000Z", "message": {"content": "brief " + PROBE}},
            assistant(100_000, "toolu_sub"),
        ])
        self.state = os.path.join(self.tmp, "state")
        self.cfg_path = os.path.join(self.tmp, "cfg", "context_guard.json")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def cfg(self, **kw):
        os.makedirs(os.path.dirname(self.cfg_path), exist_ok=True)
        with open(self.cfg_path, "w") as f:
            json.dump(kw, f)
        return cg.load_config(self.cfg_path)

    def args(self, *extra):
        return cg.parse_args(["--state-dir", self.state, *extra])

    def inp(self, sub=False, **kw):
        d = {"session_id": "sess", "transcript_path": self.main_tp, "hook_event_name": "PostToolUse",
             "tool_name": "Bash", "tool_use_id": "toolu_sub" if sub else "toolu_main", "duration_ms": 1000}
        if sub:
            d.update(agent_id=AID, agent_type="engineer")
        d.update(kw)
        return d

    def note(self, out):
        return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else ""

    def set_ctx(self, path, prompt):
        with open(path, "a") as f:
            f.write(json.dumps(assistant(prompt)) + "\n")

    def sub_run(self, cfg, now=NOW, *extra, **kw):
        return self.note(cg.run(self.inp(sub=True, **kw), self.args(*extra), cfg, now))


class TestTranscript(Base):
    def test_paths(self):
        self.assertEqual(cg.agent_transcript(self.inp()), self.main_tp)
        self.assertEqual(cg.agent_transcript(self.inp(sub=True)), self.sub_tp)
        self.assertEqual(cg.agent_transcript(self.inp(sub=True, transcript_path=self.sub_tp)), self.sub_tp)

    def test_scan_skips_synthetic_and_finds_tool_use(self):
        ctx, ts = cg.scan(cg.read_tail(self.main_tp, 1 << 20), "toolu_main")
        self.assertEqual(ctx, 240_005)
        self.assertEqual(ts, "2026-10-09T20:01:00.000Z")

    def test_cut_first_line_is_dropped(self):
        lines = cg.read_tail(self.main_tp, os.path.getsize(self.main_tp) - 10)
        self.assertTrue(lines)
        for ln in lines:
            json.loads(ln)                     # every kept line is whole


class TestContextNotices(Base):
    def test_defaults(self):
        c = cg.load_config(os.path.join(self.tmp, "absent.json"))
        self.assertEqual((c["lane_limit"], c["wrapup_margin"], c["coordinator_compact_at"], c["wait_cap_ms"]),
                         (150_000, 20_000, 180_000, 270_000))
        self.assertEqual(cg.limits_for(c, "anything"), (None, 130_000, 150_000))

    def test_coordinator_fires_once(self):
        c = self.cfg()
        out = cg.run(self.inp(), self.args(), c, NOW)
        self.assertIn("[context-guard] context 240k: compact at the next natural point", self.note(out))
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["hookEventName"], "PostToolUse")
        self.assertNotIn("decision", json.loads(out))
        self.assertEqual(cg.run(self.inp(), self.args(), c, NOW + 1), "")

    def test_coordinator_notice_off(self):
        self.assertEqual(cg.run(self.inp(), self.args(), self.cfg(coordinator_compact_at=0), NOW), "")

    def test_sub_wrapup_then_limit_then_rearm(self):
        c = self.cfg()
        self.set_ctx(self.sub_tp, 125_000)
        self.assertEqual(self.sub_run(c), "")                               # wrap-up point is 130k
        self.set_ctx(self.sub_tp, 135_000)
        out = self.sub_run(c)
        self.assertIn("context 135k: 15k left before your 150k limit", out)
        self.assertIn("start nothing new", out)
        self.assertEqual(self.sub_run(c), "")                               # once
        self.set_ctx(self.sub_tp, 153_000)
        out = self.sub_run(c)
        self.assertIn("context 153k is past your 150k limit: stop now", out)
        self.assertIn("HANDOFF ready", out)
        self.assertEqual(self.sub_run(c), "")
        self.set_ctx(self.sub_tp, 30_000)                                   # compaction
        self.assertEqual(self.sub_run(c), "")
        self.set_ctx(self.sub_tp, 131_000)
        self.assertIn("context 131k: 19k left", self.sub_run(c))

    def test_first_call_past_the_wrapup_point_fires_wrapup(self):
        self.assertEqual(self.sub_run(self.cfg()), "")                      # fixture: 100k, below 130k
        shutil.rmtree(self.state)                                           # a first call again
        self.set_ctx(self.sub_tp, 140_000)
        self.assertIn("context 140k: 10k left before your 150k limit", self.sub_run(self.cfg()))

    def test_jump_past_both_fires_limit_only(self):
        self.set_ctx(self.sub_tp, 170_000)
        out = self.sub_run(self.cfg())
        self.assertIn("past your 150k limit", out)
        self.assertNotIn("left before", out)

    def test_sub_and_coordinator_states_are_separate(self):
        c = self.cfg()
        self.set_ctx(self.sub_tp, 140_000)                                  # past the wrap-up point
        self.assertTrue(cg.run(self.inp(sub=True), self.args(), c, NOW))
        self.assertTrue(cg.run(self.inp(), self.args(), c, NOW))

    def test_below_threshold_silent(self):
        self.assertEqual(self.sub_run(self.cfg(lane_limit=400_000)), "")

    def test_lane_limit_zero_is_off(self):
        self.set_ctx(self.sub_tp, 900_000)
        self.assertEqual(self.sub_run(self.cfg(lane_limit=0)), "")

    def test_limit_per_agent_type(self):
        c = self.cfg(agent_types={"batch-worker": {"limit": 80_000}, "engineer": {"limit": 160_000},
                                  "tight": {"limit": 50_000, "wrapup_margin": 0}})
        self.assertEqual(cg.limits_for(c, "batch-worker"), (None, 60_000, 80_000))
        self.assertEqual(cg.limits_for(c, "tight"), (None, 50_000, 50_000))
        self.assertEqual(cg.limits_for(c, "other"), (None, 130_000, 150_000))
        self.assertEqual(self.sub_run(c), "")                               # engineer at 100k: wrap-up is 140k
        out = self.sub_run(c, agent_type="batch-worker")
        self.assertIn("context 100k is past your 80k limit", out)

    def test_skip_agent_types(self):
        c = self.cfg(skip_agent_types=["engineer"])
        self.set_ctx(self.sub_tp, 300_000)
        self.assertEqual(self.sub_run(c), "")
        d = pre(timeout=600_000)
        self.assertEqual(cg.pre_tool_use(d, c), "")

    def test_notes_hint_in_notice(self):
        self.set_ctx(self.sub_tp, 140_000)                                  # past the wrap-up point
        self.assertIn("at the top of notes/<name>.md up to date", self.sub_run(self.cfg(notes_hint="notes/<name>.md")))

    def test_bad_config_values_fall_back(self):
        c = self.cfg(lane_limit="170000", wrapup_margin=-5, wait_cap_ms=True, agent_types=[1], skip_agent_types="x",
                     slow_call_s=None, log=7)
        d = dict(cg.DEFAULTS)
        self.assertEqual({k: c[k] for k in d}, d)
        with open(self.cfg_path, "w") as f:
            f.write("{not json")
        self.assertEqual(cg.load_config(self.cfg_path)["lane_limit"], 150_000)

    def test_slow_call_with_cooldown(self):
        c = self.cfg(coordinator_compact_at=0)
        out = self.note(cg.run(self.inp(duration_ms=420_000), self.args(), c, NOW))
        self.assertIn("that call took 420 s, longer than the 5-minute prompt cache", out)
        self.assertEqual(cg.run(self.inp(duration_ms=420_000), self.args(), c, NOW + 600), "")
        self.assertIn("call took", self.note(cg.run(self.inp(duration_ms=420_000), self.args(), c, NOW + 3700)))

    def test_slow_call_from_transcript_when_no_duration(self):
        c = self.cfg(coordinator_compact_at=0)
        d = self.inp()
        del d["duration_ms"]
        t0 = cg.iso_to_epoch("2026-10-09T20:01:00.000Z")
        self.assertEqual(cg.run(d, self.args(), c, t0 + 100), "")
        self.assertIn("call took 360 s", self.note(cg.run(d, self.args(), c, t0 + 360)))

    def test_prompt_filter(self):
        a = self.args("--only-if-prompt-contains", PROBE)
        c = self.cfg()
        self.set_ctx(self.sub_tp, 140_000)                                  # past the wrap-up point
        self.assertEqual(cg.run(self.inp(), a, c, NOW), "")                 # the main brief lacks the token
        self.assertTrue(cg.run(self.inp(sub=True), a, c, NOW))

    def test_log_line_and_relative_paths(self):
        c = self.cfg(log="logs/notices.jsonl", state_dir="st")
        cg.run(self.inp(), cg.parse_args([]), c, NOW)
        base = os.path.dirname(self.cfg_path)
        with open(os.path.join(base, "logs", "notices.jsonl")) as f:
            rec = json.loads(f.read())
        self.assertEqual((rec["ctx"], rec["source"]), (240_005, "config"))
        self.assertTrue(os.path.isfile(os.path.join(base, "st", "sess_main.json")))


class TestOverrides(Base):
    """The coordinator's dynamic layer (context_guard.overrides.json next to the config)."""

    def write_ov(self, data=None, body=None):
        c = self.cfg()
        p = os.path.join(os.path.dirname(self.cfg_path), "context_guard.overrides.json")
        with open(p, "w") as f:
            f.write(body if body is not None else json.dumps(data))
        return c

    def state_of(self):
        with open(os.path.join(self.state, f"sess_{AID}.json")) as f:
            return json.load(f)

    def test_agent_override_with_note(self):
        log = os.path.join(self.tmp, "log.jsonl")
        c = self.write_ov({"agents": {AID: {"limit": 200_000, "wrapup": 170_000, "note": 100_000,
                                           "label": "sweep3", "until": UNTIL_LATER}}})
        run = lambda t: self.note(cg.run(self.inp(sub=True), self.args("--log", log), c, t))  # noqa: E731
        out = run(NOW)                                                       # fixture: 100k
        self.assertIn("context 100k: update the HANDOFF block", out)
        self.assertIn("then continue (limit 200k)", out)
        self.assertEqual(run(NOW + 1), "")                                   # note: once
        self.set_ctx(self.sub_tp, 150_000)
        self.assertEqual(run(NOW + 2), "")
        self.set_ctx(self.sub_tp, 175_000)
        self.assertIn("context 175k: 25k left before your 200k limit", run(NOW + 3))
        self.set_ctx(self.sub_tp, 201_000)
        self.assertIn("201k is past your 200k limit", run(NOW + 4))
        self.assertEqual(self.state_of()["th"], {"note": 100_000, "wrapup": 170_000, "limit": 200_000})
        with open(log) as f:
            recs = [json.loads(ln) for ln in f]
        self.assertEqual(len(recs), 3)
        self.assertTrue(all(r["source"] == "agent" and r["label"] == "sweep3" for r in recs))

    def test_precedence_agent_type_default_config(self):
        c = self.cfg(agent_types={"engineer": {"limit": 110_000}})
        ovs = {"default": {"limit": 180_000}, "types": {"engineer": {"limit": 160_000}},
               "agents": {AID: {"limit": 170_000}}, "coordinator": None}
        self.assertEqual(cg.resolve(c, ovs, AID, "engineer"), (None, 150_000, 170_000, None, "agent"))
        self.assertEqual(cg.resolve(c, ovs, "other", "engineer"), (None, 140_000, 160_000, None, "type"))
        self.assertEqual(cg.resolve(c, ovs, "other", "worker"), (None, 160_000, 180_000, None, "default"))
        none = {"default": None, "types": {}, "agents": {}, "coordinator": None}
        self.assertEqual(cg.resolve(c, none, AID, "engineer"), (None, 90_000, 110_000, None, "config"))
        self.assertEqual(cg.resolve(c, none, AID, "worker"), (None, 130_000, 150_000, None, "config"))

    def test_default_override_for_all_lanes(self):
        c = self.write_ov({"default": {"limit": 200_000}})
        self.set_ctx(self.sub_tp, 140_000)                                  # past the config's wrap-up point (130k)
        self.assertEqual(self.sub_run(c), "")                               # wrap-up is 180k now
        self.set_ctx(self.sub_tp, 181_000)
        self.assertIn("context 181k: 19k left before your 200k limit", self.sub_run(c))

    def test_coordinator_override(self):
        c = self.write_ov({"coordinator": {"compact_at": 250_000, "until": UNTIL_LATER}})
        self.assertEqual(cg.run(self.inp(), self.args(), c, NOW), "")       # 240k < 250k
        after = cg.iso_to_epoch("2026-10-13T14:00:01Z")                     # expired: 180k again
        self.assertIn("240k: compact", self.note(cg.run(self.inp(), self.args(), c, after)))

    def assert_config_limits(self, c, now=NOW):
        self.set_ctx(self.sub_tp, 140_000)             # past the config's wrap-up point; an override that
                                                       # was applied by mistake would answer differently
        self.assertIn("context 140k: 10k left before your 150k limit", self.sub_run(c, now))

    def test_expired_entry_is_ignored(self):
        c = self.write_ov({"agents": {AID: {"limit": 200_000, "until": UNTIL_LATER}}})
        self.assert_config_limits(c, now=cg.iso_to_epoch("2026-10-13T14:00:00Z"))   # = 16:00 +02:00

    def test_until_without_offset_is_ignored(self):
        self.assert_config_limits(self.write_ov({"agents": {AID: {"limit": 200_000, "until": "2099-01-01T00:00:00"}}}))

    def test_bad_file_and_bad_entries_fall_back(self):
        self.assert_config_limits(self.write_ov(body="{not json"))
        for bad in ({"limit": "200000"}, {"limit": 100_000, "wrapup": 150_000}, {"wrapup": 90_000}, "x", 5):
            shutil.rmtree(self.state)
            self.assert_config_limits(self.write_ov({"agents": {AID: bad}}))

    def test_missing_file_and_unlisted_agent(self):
        self.assert_config_limits(self.cfg(overrides_file="absent.json"))
        shutil.rmtree(self.state)
        self.assert_config_limits(self.write_ov({"agents": {"aother": {"limit": 200_000}}}))
        shutil.rmtree(self.state)
        self.assert_config_limits(self.cfg(overrides_file=""))

    def test_agent_is_told_when_its_limit_changes(self):
        c = self.cfg()
        self.set_ctx(self.sub_tp, 155_000)
        self.assertIn("155k is past your 150k limit", self.sub_run(c))      # told to stop at 150k
        c = self.write_ov({"default": {"limit": 200_000}})                   # the coordinator raises it
        out = self.sub_run(c, NOW + 5)
        self.assertIn("the coordinator changed your context limit from 150k to 200k (wrap-up from 180k)", out)
        self.assertNotIn("left before", out)                                 # 155k is below the new wrap-up
        self.assertEqual(self.sub_run(c, NOW + 6), "")                       # told once
        self.set_ctx(self.sub_tp, 185_000)
        self.assertIn("context 185k: 15k left before your 200k limit", self.sub_run(c, NOW + 7))
        c = self.write_ov({"default": {"limit": 100_000}})                   # lowered below the context
        out = self.sub_run(c, NOW + 8)
        self.assertIn("changed your context limit from 200k to 100k", out)
        self.assertIn("185k is past your 100k limit: stop now", out)

    def test_first_call_records_the_limit_silently(self):
        c = self.cfg(lane_limit=400_000)
        self.assertEqual(self.sub_run(c), "")
        self.assertEqual(self.state_of()["limit"], 400_000)


class TestControl(Base):
    CTL = os.path.join(os.path.dirname(HERE), "context_guard_ctl.py")

    def ctl(self, *argv):
        return subprocess.run([sys.executable, self.CTL, "--config", self.cfg_path, *argv],
                              capture_output=True, text=True, timeout=10)

    def ov_file(self):
        with open(os.path.join(os.path.dirname(self.cfg_path), "context_guard.overrides.json")) as f:
            return json.load(f)

    def test_set_show_clear(self):
        c = self.cfg()
        r = self.ctl("set", "--default", "--limit", "160000", "--for", "3h", "--reason", "wave 3")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("set default: limit 160k", r.stdout)
        self.assertEqual(self.ctl("set", "--type", "batch-worker", "--limit", "80000").returncode, 0)
        self.assertEqual(self.ctl("set", "--agent", AID, "--limit", "200000", "--note", "120000",
                                  "--label", "lane-x", "--for", "90m").returncode, 0)
        self.assertEqual(self.ctl("set", "--coordinator", "--limit", "250000", "--for", "30m").returncode, 0)
        d = self.ov_file()
        self.assertEqual((d["default"]["limit"], d["default"]["reason"]), (160_000, "wave 3"))
        self.assertEqual(d["types"]["batch-worker"], {"limit": 80_000})
        self.assertEqual((d["agents"][AID]["limit"], d["agents"][AID]["label"]), (200_000, "lane-x"))
        self.assertEqual(d["coordinator"]["compact_at"], 250_000)
        now = time.time()
        ovs = cg.load_overrides(c, now)
        self.assertEqual(cg.resolve(c, ovs, AID, "engineer")[:3], (120_000, 180_000, 200_000))
        self.assertEqual(cg.resolve(c, ovs, "x", "batch-worker")[1:3], (60_000, 80_000))
        self.assertEqual(cg.resolve(c, ovs, "x", "engineer")[1:3], (140_000, 160_000))
        self.assertEqual(cg.compact_point(c, ovs), 250_000)
        self.assertEqual(cg.resolve(c, cg.load_overrides(c, now + 4 * 3600), "x", "engineer")[2], 150_000)
        out = self.ctl("show").stdout
        self.assertIn("in effect now: lanes limit 160k, wrap-up from 140k (default)", out)
        self.assertIn("agent " + AID, out)
        self.assertEqual(self.ctl("clear", "--agent", AID).returncode, 0)
        self.assertNotIn("agents", self.ov_file())
        self.assertEqual(self.ctl("clear", "--all").returncode, 0)
        self.assertEqual(self.ov_file(), {})
        with open(os.path.join(os.path.dirname(self.cfg_path), "context_guard.changes.jsonl")) as f:
            self.assertEqual(len(f.readlines()), 6)

    def test_refuses_bad_input(self):
        self.cfg()
        for argv in (["set", "--limit", "100000"], ["set", "--default"], ["set", "--default", "--type", "x", "--limit", "5"],
                     ["set", "--default", "--limit", "100000", "--wrapup", "150000"],
                     ["set", "--default", "--limit", "100000", "--for", "soon"],
                     ["set", "--default", "--limit", "100000", "--until", "2099-01-01T00:00:00"],
                     ["set", "--coordinator", "--limit", "250000", "--note", "5"]):
            r = self.ctl(*argv)
            self.assertNotEqual(r.returncode, 0, argv)
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(self.cfg_path), "context_guard.overrides.json")))

    def test_clear_expired(self):
        self.cfg()
        p = os.path.join(os.path.dirname(self.cfg_path), "context_guard.overrides.json")
        with open(p, "w") as f:
            json.dump({"default": {"limit": 150_000, "until": "2020-01-01T00:00:00+00:00"},
                       "agents": {"a": {"limit": 1, "until": "2020-01-01T00:00:00+00:00"}, "b": {"limit": 2}}}, f)
        self.assertEqual(self.ctl("clear", "--expired").returncode, 0)
        self.assertEqual(self.ov_file(), {"agents": {"b": {"limit": 2}}})


def pre(sub=True, **ti):
    d = {"session_id": "sess", "transcript_path": "/nonexistent/sess.jsonl", "hook_event_name": "PreToolUse",
         "tool_name": "Bash", "tool_use_id": "toolu_pre", "tool_input": {"command": "sleep 1", **ti}}
    if sub:
        d.update(agent_id=AID, agent_type="engineer")
    return d


class TestPreToolUse(unittest.TestCase):
    CFG = cg.load_config("/nonexistent/context_guard.json")

    def deny(self, d, cfg=None):
        out = cg.pre_tool_use(d, cfg or self.CFG)
        if not out:
            return False
        h = json.loads(out)["hookSpecificOutput"]
        self.assertEqual((h["hookEventName"], h["permissionDecision"]), ("PreToolUse", "deny"))
        self.assertIn("outlives the 5-minute prompt cache", h["permissionDecisionReason"])
        return True

    def test_deny_long_foreground(self):
        self.assertTrue(self.deny(pre(timeout=600_000)))
        self.assertTrue(self.deny(pre(timeout=270_001)))
        self.assertTrue(self.deny(pre(timeout=600_000, run_in_background=False)))
        self.assertTrue(self.deny(pre(timeout="600000")))

    def test_allow(self):
        for ti in [{}, {"timeout": 270_000}, {"timeout": 120_000}, {"timeout": 600_000, "run_in_background": True},
                   {"timeout": 600_000, "run_in_background": "true"}, {"timeout": None}, {"timeout": True},
                   {"timeout": "abc"}]:
            self.assertFalse(self.deny(pre(**ti)), ti)

    def test_coordinator_and_other_tools_pass(self):
        self.assertFalse(self.deny(pre(sub=False, timeout=600_000)))
        d = pre(timeout=600_000)
        d["tool_name"] = "Read"
        self.assertFalse(self.deny(d))

    def test_renamed_bash_tool_is_covered(self):
        d = pre(timeout=600_000)
        d["tool_name"] = "mcp__proxy__some_Bash"
        self.assertTrue(self.deny(d))
        d["tool_name"] = "BashOutput"
        self.assertFalse(self.deny(d))

    def test_cap_for_coordinator_and_custom_cap(self):
        cfg = dict(self.CFG, wait_cap_applies_to_coordinator=True, wait_cap_ms=100_000)
        self.assertTrue(self.deny(pre(sub=False, timeout=120_000), cfg))
        self.assertFalse(self.deny(pre(sub=False, timeout=100_000), cfg))
        self.assertIn("timeout <= 100000", cg.pre_tool_use(pre(timeout=120_000), cfg))
        self.assertFalse(self.deny(pre(timeout=600_000), dict(self.CFG, wait_cap_ms=0)))

    def test_malformed_tool_input(self):
        for ti in [None, "x", [1], 5]:
            d = pre()
            d["tool_input"] = ti
            self.assertEqual(cg.pre_tool_use(d, self.CFG), "")


class TestBackgroundReminder(Base):
    def bg(self, **kw):
        d = self.inp(sub=True, tool_input={"command": "x", "run_in_background": True})
        d.update(kw)
        return d

    def quiet(self):
        return self.cfg(lane_limit=900_000, coordinator_compact_at=0)

    def test_once_per_job_and_cooldown(self):
        c = self.quiet()
        run = lambda tu, t: self.note(cg.run(self.bg(tool_use_id=tu), self.args(), c, t))   # noqa: E731
        out = run("t1", NOW)
        self.assertIn("a background job is running", out)
        self.assertIn("checks of at most 240 s", out)
        self.assertIn("a fresh agent collects the result", out)
        self.assertEqual(run("t1", NOW + 2000), "")                          # same job
        self.assertEqual(run("t2", NOW + 60), "")                            # rate-limited
        self.assertIn("background job", run("t3", NOW + 1801))

    def test_background_task_id_in_result(self):
        d = self.inp(sub=True, tool_input={"command": "x"},
                     tool_response={"stdout": "", "backgroundTaskId": "b635ubahy"})
        self.assertIn("background job", self.note(cg.run(d, self.args(), self.quiet(), NOW)))

    def test_renamed_bash_tool(self):
        d = self.bg(tool_name="mcp__proxy__some_Bash")
        self.assertIn("background job", self.note(cg.run(d, self.args(), self.quiet(), NOW)))

    def test_foreground_and_coordinator_silent(self):
        c = self.quiet()
        self.assertEqual(cg.run(self.inp(sub=True, tool_input={"command": "x"}), self.args(), c, NOW), "")
        self.assertEqual(cg.run(self.inp(tool_input={"command": "x", "run_in_background": True}),
                                self.args(), c, NOW), "")

    def test_no_transcript_still_reminds_never_blocks(self):
        out = cg.run(self.bg(transcript_path="/nonexistent/s.jsonl"), self.args(), self.quiet(), NOW)
        self.assertIn("background job", self.note(out))
        self.assertNotIn("decision", json.loads(out))


class TestNeverFails(Base):
    def run_cli(self, stdin, *argv):
        self.cfg()
        return subprocess.run([sys.executable, SCRIPT, "--config", self.cfg_path, "--state-dir", self.state, *argv],
                              input=stdin, capture_output=True, text=True, timeout=10)

    def test_garbage_inputs_exit_0_silent(self):
        for stdin in ["", "not json", "[]", json.dumps({"transcript_path": "/nonexistent.jsonl"}),
                      json.dumps(self.inp(transcript_path=12))]:
            r = self.run_cli(stdin)
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""), stdin)

    def test_pre_cli_deny_and_pass(self):
        log = os.path.join(self.tmp, "log.jsonl")
        r = self.run_cli(json.dumps(pre(timeout=600_000)), "--log", log)
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        self.assertEqual(json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")
        with open(log) as f:
            self.assertTrue(json.loads(f.read())["deny"])
        for d in [pre(sub=False, timeout=600_000), pre(timeout=1000), pre(timeout=600_000, run_in_background=True)]:
            r = self.run_cli(json.dumps(d))
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))

    def test_pre_malformed_fails_open(self):
        bad = [json.dumps({"hook_event_name": "PreToolUse"}),
               json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash", "agent_id": AID,
                           "tool_input": "garbage"}),
               json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash", "agent_id": AID,
                           "tool_input": {"timeout": {"x": 1}}}),
               '{"hook_event_name": "PreToolUse", "tool_name": "Bash", "agent_id": "a", "tool_input": {"timeo']
        for stdin in bad:
            r = self.run_cli(stdin)
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""), stdin)

    def test_bad_args_exit_0(self):
        r = self.run_cli(json.dumps(self.inp()), "--no-such-flag", "1")
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_unwritable_state_exit_0(self):
        r = subprocess.run([sys.executable, SCRIPT, "--state-dir", "/dev/null/x"], input=json.dumps(self.inp()),
                           capture_output=True, text=True, timeout=10)
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_missing_config_uses_defaults(self):
        r = subprocess.run([sys.executable, SCRIPT, "--config", "/nonexistent/c.json", "--state-dir", self.state],
                           input=json.dumps(self.inp()), capture_output=True, text=True, timeout=10)
        self.assertEqual(r.returncode, 0)
        self.assertIn("context 240k: compact", r.stdout)

    def test_cli_emits_and_is_fast(self):
        big = os.path.join(self.tmp, "big.jsonl")         # 30 MB transcript: only the tail is read
        with open(big, "w") as f:
            line = json.dumps({"type": "user", "message": {"content": "y" * 1000}}) + "\n"
            f.write(line * 30_000)
        with open(big, "a") as f, open(self.main_tp) as g:
            f.write(g.read())
        t = time.time()
        r = self.run_cli(json.dumps(self.inp(transcript_path=big)))
        dt = time.time() - t
        self.assertEqual(r.returncode, 0)
        self.assertIn("compact at the next natural point", r.stdout)
        self.assertLess(dt, 1.0)


if __name__ == "__main__":
    unittest.main()
