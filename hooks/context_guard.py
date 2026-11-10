#!/usr/bin/env python3
"""context_guard -- Claude Code hook: context-size notices, a foreground wait cap, cache reminders.

Register the same command for two hook events (see settings.example.json).

PreToolUse (all tools, matcher "*"):
  - denies a foreground Bash call whose `timeout` is above the wait cap (default 270 s). Such a
    call outlives the 5-minute prompt cache, so the agent's next call would write its whole
    context again. Sub-agents only, unless the config says otherwise.
  - hard stop: denies every tool call of a sub-agent whose context is at or past its limit plus
    `hard_stop_margin` and that has used its `hard_stop_grace_calls`. Decided from the state file;
    a raised limit or a fallen context lifts it at the next call.

PostToolUse (all tools): puts a one-line "[context-guard] ..." notice next to the tool result when
  - a sub-agent comes within the wrap-up margin of its context limit, and again at the limit;
    past the limit, again for every `stop_repeat_every` tokens; at the hard stop, with the
    grace calls left;
  - the coordinator (the main session) reaches its compaction point;
  - one of the coordinator's lanes passed its limit or reached its hard stop (with the command
    that raises its limit);
  - a tool call took longer than the prompt cache lives;
  - a sub-agent started a background job (the notice says how to wait for it).

Agents cannot see their own context size; this hook tells them. A context notice fires once per
(session, agent) and re-arms after the context fell again (compaction). A second run for the same
tool call (the hook registered twice) does nothing.

Config: --config, then $CONTEXT_GUARD_CONFIG, then the first .claude/hooks/context_guard.json
walking up from $CLAUDE_PROJECT_DIR, then context_guard.json next to this script. With
--require-config the hook does nothing when none of these exists. Limits come from two files,
both read on every call, so a change takes effect at the next tool call of every running agent:
  - context_guard.json: the project's base config (default lane limit, limits per agent type);
  - context_guard.overrides.json: the coordinator's dynamic layer (a limit for all lanes, for one
    agent type or for one agent, each with an optional expiry, and its own compaction point).
    The coordinator writes it with context_guard_ctl.py. An agent whose limit changed is told so.

The hook must never get in the way of a tool call: every error path exits 0 with no output, the
only block is the PreToolUse deny, and it uses no network. Python 3.8+, standard library only.
"""
from __future__ import annotations

import json
import os
import shlex
import sys
import time
from types import SimpleNamespace

DEFAULTS = {
    "lane_limit": 150_000,              # sub-agent: stop here (0 = no context notices for sub-agents)
    "wrapup_margin": 20_000,            # sub-agent: wrap-up notice this many tokens before the limit
    "coordinator_compact_at": 180_000,  # main session: compaction notice (0 = off)
    "agent_types": {},                  # {"<agent type>": {"limit": int, "wrapup_margin": int, "deliverable": "report"}}
    "skip_agent_types": [],             # agent types this hook leaves alone
    "overrides_file": "context_guard.overrides.json",   # the coordinator's dynamic layer, see load_overrides()
    "wait_cap_ms": 270_000,             # PreToolUse: foreground Bash timeout above this is denied
    "wait_cap_applies_to_coordinator": False,
    "slow_call_s": 300,                 # notice when a tool call took longer than this
    "slow_call_cooldown_s": 3600,       # ... at most once per agent in this time
    "bg_reminder_cooldown_s": 1800,     # background-job reminder: at most once per agent in this time
    "rearm_margin": 10_000,             # a notice re-arms when the context fell this far below it
    "notes_hint": "your notes file",    # how the notices name the place of the HANDOFF block
    "state_dir": "~/.cache/context-guard",
    "log": "",                          # JSON lines: one per notice and per denial ("" = off)
    "stop_repeat_every": 10_000,        # sub-agent past its limit: repeat the stop notice per this growth (0 = once)
    "hard_stop_margin": 20_000,         # sub-agent: refuse tool calls from limit + this (0 = no hard stop)
    "hard_stop_grace_calls": 10,        # ... after this many further tool calls (notes, commit, push)
    "coordinator_lane_alerts": True,    # main session: one line per lane past its limit / at its hard stop
}

CONFIG_MAX_BYTES = 64 * 1024
TAIL_BYTES = 256 * 1024                 # first read; one retry with TAIL_MAX if no usage line was found
TAIL_MAX = 2 * 1024 * 1024
STATE_TTL_S = 7 * 86400
BG_JOBS_KEPT = 50                       # job ids remembered for the once-per-job rule

WRAPUP = ("context {k}k: {m}k left before your {f}k limit. Finish the step in hand and start nothing "
          "new. Bring the HANDOFF block at the top of {notes} up to date, commit what is tested and report.")
LIMIT = ("context {k}k is past your {f}k limit: stop now. Update the HANDOFF block at the top of {notes} "
         "(state, commits, live job ids, numbered next steps), commit what is tested and end with "
         "\"HANDOFF ready\".")
NOTE = ("context {k}k: update the HANDOFF block at the top of {notes} now, so that a later handoff stays "
        "cheap, then continue (limit {f}k).")
MAIN = ("context {k}k: compact at the next natural point (after a push or a fully processed report), "
        "with a carry block that names the next step.")
CHANGED = ("the coordinator changed your context limit from {o}k to {f}k (wrap-up from {w}k). This replaces "
           "the limit in your brief and any earlier notice.")
SLOW = ("that call took {s} s, longer than the 5-minute prompt cache, so your next call writes your whole "
        "context again. Keep foreground waits at 240 s per call; start longer work in the background and poll it.")
BG = ("a background job is running. Poll it in the foreground with checks of at most 240 s (for example "
      "bgwait.sh <log> 240). Do not end your turn to be woken later: after 5 idle minutes your whole context "
      "is written again. If the job will run longer than about 45 minutes, update your HANDOFF, tell the "
      "coordinator and end; a fresh agent collects the result.")
DENY = ("Bash timeout above {s} s outlives the 5-minute prompt cache: your next call would write your whole "
        "context again. Re-run with timeout <= {ms}, or set run_in_background and poll with checks of at "
        "most 240 s.")
REPEAT = ("context {k}k is {p}k past your {f}k limit: stop now. Update the HANDOFF block at the top of {notes}, "
          "commit what is tested and end with \"HANDOFF ready\".{h}")
REPEAT_HARD = " From {h}k your tool calls will be refused."
HARD_START = ("context {k}k: hard stop ({f}k limit + {m}k). You have {n} more tool calls for notes, commit, "
              "push and stopping your jobs; after that every tool call is refused. End with your handoff report.")
HARD_LEFT = "hard stop: {n} tool calls left, then every tool call is refused. End with your handoff report."
HARD_DENY = ("context {k}k against your {f}k limit: your tool calls are refused. End your turn now with your "
             "handoff report as your final message. The coordinator can lift this by raising your limit.")
# Report lanes ("deliverable": "report" in their agent type): a read-only agent whose only output is a
# report file has no HANDOFF block and nothing to commit, so its notices ask for the report instead.
R_WRAPUP = ("context {k}k: {m}k left before your {f}k limit. Open no new sources. Write your report file now "
            "from what you have, and list in it what you did not read.")
R_LIMIT = ("context {k}k is past your {f}k limit: stop now. Finish your report file (findings, what you left "
           "unread, open questions) and end your turn.")
R_NOTE = ("context {k}k: write what you have found so far into your report file now, then continue "
          "(limit {f}k).")
R_REPEAT = ("context {k}k is {p}k past your {f}k limit: stop now. Finish your report file (findings, what you "
            "left unread, open questions) and end your turn.{h}")
R_HARD_START = ("context {k}k: hard stop ({f}k limit + {m}k). You have {n} more tool calls to finish your "
                "report file; after that every tool call is refused.")
R_HARD_LEFT = ("hard stop: {n} tool calls left to finish your report file, then every tool call is refused.")
R_HARD_DENY = ("context {k}k against your {f}k limit: your tool calls are refused. End your turn now and say "
               "in your final message what is missing from your report file. The coordinator can lift this "
               "by raising your limit.")
R_BG = ("a background job is running. Poll it in the foreground with checks of at most 240 s (for example "
        "bgwait.sh <log> 240). Do not end your turn to be woken later: after 5 idle minutes your whole context "
        "is written again. If the job will run longer than about 45 minutes, write what you have into your "
        "report file, tell the coordinator and end.")
LANE_OVER = ("lane {name} is past its limit: context {k}k, limit {f}k. To raise it: {cmd}")
LANE_HARD = ("lane {name} has reached its hard stop (context {k}k, limit {f}k): its tool calls are refused "
             "after its grace calls. To lift it: {cmd}")
LANE_FRESH_S = 900                      # coordinator alerts only for lanes updated this recently
LANE_SCAN_EVERY_S = 30                  # ... and scan for them at most this often
LANE_ALERTS_KEPT = 200
TUIDS_KEPT = 32                         # tool calls remembered, so that a second run for one of them does nothing

FLAGS = ("--config", "--state-dir", "--log", "--trace", "--tag", "--only-if-prompt-contains")
BOOL_FLAGS = ("--require-config",)


def _true(v) -> bool:
    return v is True or (isinstance(v, str) and v.strip().lower() == "true")


def _num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            return None
    return None


def _int(v, minimum=0):
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= minimum else None


def is_bash(name) -> bool:
    """Bash, or a Bash tool that a wrapper renamed (for example "mcp__<server>__<x>_Bash")."""
    return isinstance(name, str) and (name == "Bash" or name.endswith("_Bash"))


def parse_args(argv) -> SimpleNamespace:
    """--flag value pairs only. No argparse: this runs on every tool call."""
    a = {f[2:].replace("-", "_"): "" for f in FLAGS}
    a.update({f[2:].replace("-", "_"): False for f in BOOL_FLAGS})
    i = 0
    while i < len(argv):
        if argv[i] in BOOL_FLAGS:
            a[argv[i][2:].replace("-", "_")] = True
            i += 1
            continue
        if argv[i] not in FLAGS or i + 1 >= len(argv):
            raise ValueError("bad argument: " + argv[i])
        a[argv[i][2:].replace("-", "_")] = argv[i + 1]
        i += 2
    return SimpleNamespace(**a)


def find_up(start, rel: str):
    """The first `rel` below `start` or one of its parent folders, up to the filesystem root, else None."""
    if not start:
        return None
    d = os.path.abspath(os.path.expanduser(start))
    while True:
        p = os.path.join(d, rel)
        if os.path.isfile(p):
            return p
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


PROJECT_CONFIG = os.path.join(".claude", "hooks", "context_guard.json")


def config_path(arg: str, extra_starts=()) -> str:
    """--config, then $CONTEXT_GUARD_CONFIG, then the first .claude/hooks/context_guard.json walking up
    from $CLAUDE_PROJECT_DIR (and from each of `extra_starts`), then the file beside this script."""
    if arg or os.environ.get("CONTEXT_GUARD_CONFIG"):
        return arg or os.environ["CONTEXT_GUARD_CONFIG"]
    for start in (os.environ.get("CLAUDE_PROJECT_DIR"),) + tuple(extra_starts):
        p = find_up(start, PROJECT_CONFIG)
        if p:
            return p
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "context_guard.json")


def load_config(path: str) -> dict:
    """Defaults, overlaid with the well-typed keys of the config file. Any problem: defaults."""
    cfg = dict(DEFAULTS)
    cfg["_dir"] = os.path.dirname(os.path.abspath(path)) if path else ""
    cfg["_path"] = os.path.abspath(path) if path else ""
    try:
        with open(path, "rb") as f:
            raw = f.read(CONFIG_MAX_BYTES + 1)
        d = json.loads(raw) if len(raw) <= CONFIG_MAX_BYTES else None
    except Exception:
        return cfg
    if not isinstance(d, dict):
        return cfg
    for key, default in DEFAULTS.items():
        v = d.get(key)
        if isinstance(default, bool):
            ok = isinstance(v, bool)
        elif isinstance(default, int):
            ok = _int(v) is not None
        elif isinstance(default, list):
            ok = isinstance(v, list) and all(isinstance(x, str) for x in v)
        else:
            ok = isinstance(v, type(default))
        if ok:
            cfg[key] = v
    return cfg


def _path(cfg: dict, value: str) -> str:
    """~ expanded; a relative path is relative to the config file's folder."""
    if not value:
        return ""
    p = os.path.expanduser(value)
    return p if os.path.isabs(p) else os.path.join(cfg.get("_dir") or "", p)


def _wrapup(limit: int, margin: int) -> int:
    return limit - margin if 0 < margin < limit else limit


def limits_for(cfg: dict, agent_type) -> tuple:
    """(note, wrapup, limit) of a sub-agent from the base config; limit 0 = no context notices."""
    limit, margin = cfg["lane_limit"], cfg["wrapup_margin"]
    e = cfg["agent_types"].get(agent_type) if isinstance(agent_type, str) else None
    if isinstance(e, dict):
        if _int(e.get("limit")) is not None:
            limit = e["limit"]
        if _int(e.get("wrapup_margin")) is not None:
            margin = e["wrapup_margin"]
    return None, _wrapup(limit, margin), limit


def is_report(cfg: dict, agent_type) -> bool:
    """True when the agent type's deliverable is a report file ("deliverable": "report"): its notices
    ask for the report instead of a HANDOFF block and a commit. Overrides never change this."""
    e = cfg["agent_types"].get(agent_type) if isinstance(agent_type, str) else None
    return isinstance(e, dict) and e.get("deliverable") == "report"


def _active(entry, now: float):
    """The entry if it is a dict that has not expired, else None. `until`: ISO time with UTC offset."""
    if not isinstance(entry, dict):
        return None
    until = entry.get("until")
    if until is None:
        return entry
    try:
        from datetime import datetime
        t = datetime.fromisoformat(str(until).replace("Z", "+00:00"))
        if t.tzinfo is None or not now < t.timestamp():
            return None                       # a time without offset is ambiguous; expired: ignored
    except Exception:
        return None
    return entry


def load_overrides(cfg: dict, now: float) -> dict:
    """The coordinator's dynamic layer, expired entries removed:
    {"default": {...}, "types": {"<agent type>": {...}}, "agents": {"<agent id>": {...}},
     "coordinator": {"compact_at": int}}
    A limit entry is {"limit": int, "wrapup": int (optional), "note": int (optional), "label": str
    (optional), "until": "<ISO time with UTC offset>" (optional), "reason": str (optional)}.
    Fails open: a missing or bad file means no overrides."""
    out = {"default": None, "types": {}, "agents": {}, "coordinator": None}
    path = _path(cfg, cfg["overrides_file"])
    if not path:
        return out
    try:
        with open(path, "rb") as f:
            raw = f.read(CONFIG_MAX_BYTES + 1)
        d = json.loads(raw) if len(raw) <= CONFIG_MAX_BYTES else None
    except Exception:
        return out
    if not isinstance(d, dict):
        return out
    out["default"] = _active(d.get("default"), now)
    out["coordinator"] = _active(d.get("coordinator"), now)
    for group in ("types", "agents"):
        entries = d.get(group)
        if isinstance(entries, dict):
            for key, entry in entries.items():
                if _active(entry, now):
                    out[group][str(key)] = entry
    return out


def _limit_entry(entry, margin: int):
    """(note, wrapup, limit, label) from a limit entry, or None when it is not valid."""
    if not isinstance(entry, dict):
        return None
    limit = _int(entry.get("limit"), 1)
    if limit is None:
        return None
    wrapup = entry.get("wrapup")
    if wrapup is None:
        wrapup = _wrapup(limit, margin)
    if _int(wrapup, 1) is None or wrapup > limit:
        return None
    note = _int(entry.get("note"), 1)
    if note is not None and note >= wrapup:
        note = None                           # a note at or after the wrap-up notice adds nothing
    label = entry.get("label")
    return note, wrapup, limit, label if isinstance(label, str) and label.strip() else None


def resolve(cfg: dict, ovs: dict, agent_id, agent_type) -> tuple:
    """(note, wrapup, limit, label, source) of a sub-agent. The most specific valid entry wins:
    agent override, type override, default override, then the base config."""
    margin = cfg["wrapup_margin"]
    for source, entry in (("agent", ovs["agents"].get(str(agent_id)) if agent_id else None),
                          ("type", ovs["types"].get(agent_type) if isinstance(agent_type, str) else None),
                          ("default", ovs["default"])):
        got = _limit_entry(entry, margin)
        if got:
            return got + (source,)
    return limits_for(cfg, agent_type) + (None, "config")


def compact_point(cfg: dict, ovs: dict) -> int:
    """The coordinator's compaction point: its override if valid, else the base config."""
    e = ovs.get("coordinator")
    v = _int(e.get("compact_at")) if isinstance(e, dict) else None
    return cfg["coordinator_compact_at"] if v is None else v


def hard_point(cfg: dict, limit) -> int:
    """Context from which a sub-agent's tool calls are refused (after its grace calls); 0 = no hard stop."""
    m = cfg["hard_stop_margin"]
    return limit + m if limit and m > 0 else 0


def state_key(inp: dict, tag: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_"
                   for ch in f"{inp.get('session_id', 'nosession')}_{inp.get('agent_id') or 'main'}{tag}")


def read_state(sp: str) -> dict:
    try:
        with open(sp) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def hard_stop_pre(inp: dict, a: SimpleNamespace, cfg: dict, now=None):
    """PreToolUse, all tools: (stdout, log record) denying a sub-agent's call once it is past its hard
    stop and has used its grace calls; ('', None) otherwise. Decided from the state file that
    PostToolUse keeps, with the limit resolved afresh from config and overrides, so a raised limit
    lifts the block at once. Only on the way to a denial is the transcript tail read once more, so
    that a context that fell (compaction) lifts it too. Reaches the same decision when run twice."""
    now = time.time() if now is None else now
    aid = inp.get("agent_id")
    if not aid or inp.get("agent_type") in cfg["skip_agent_types"] or cfg["hard_stop_margin"] <= 0:
        return "", None
    _, _, limit, _, _ = resolve(cfg, load_overrides(cfg, now), aid, inp.get("agent_type"))
    hard = hard_point(cfg, limit)
    if not hard:
        return "", None
    state_dir = a.state_dir or _path(cfg, cfg["state_dir"])
    st = read_state(os.path.join(state_dir, state_key(inp, a.tag) + ".json"))
    h, ctx = st.get("hard"), st.get("last_ctx")
    if not isinstance(h, dict) or not isinstance(ctx, int) or ctx < hard:
        return "", None
    if h.get("limit") != limit:
        return "", None                       # the limit changed: the next PostToolUse starts a new grace count
    used = h.get("used")
    if not isinstance(used, int) or used < cfg["hard_stop_grace_calls"]:
        return "", None
    path = agent_transcript(inp)
    if not path or not os.path.isfile(path):
        return "", None                       # the lane's transcript cannot be read: never refuse blind
    fresh, _ = scan(read_tail(path, TAIL_BYTES), None)
    if fresh is not None:
        if fresh < hard:
            return "", None                   # compacted since the last tool call: lifted
        ctx = fresh
    out = json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny",
        "permissionDecisionReason": "[context-guard] " + (
            R_HARD_DENY if is_report(cfg, inp.get("agent_type")) else HARD_DENY).format(k=ctx // 1000, f=limit // 1000)}})
    return out, {"ts": now, "event": "PreToolUse", "deny": "hard_stop", "agent_id": aid,
                 "agent_type": inp.get("agent_type"), "tool": inp.get("tool_name"), "ctx": ctx, "limit": limit}


def lane_alerts(inp: dict, state: dict, state_dir: str, main_key: str, cfg: dict, now: float) -> list:
    """Main session: one line per lane of this session that has passed its limit, and one more when it
    has reached its hard stop. Each once per lane and level; only lanes updated in the last 15 minutes.
    Scans the state dir at most once per 30 s, opening only this session's recently written files."""
    if not cfg["coordinator_lane_alerts"] or now - (state.get("lane_scan_ts") or 0) < LANE_SCAN_EVERY_S:
        return []
    state["lane_scan_ts"] = now
    prefix = state_key({"session_id": inp.get("session_id"), "agent_id": "x"}, "")[:-1]
    alerted = state.get("lane_alerts")
    alerted = alerted if isinstance(alerted, dict) else {}
    out = []
    try:
        entries = list(os.scandir(state_dir))
    except OSError:
        return []
    for e in entries:
        n = e.name
        if not n.startswith(prefix) or not n.endswith(".json") or n == main_key + ".json":
            continue
        try:
            if now - e.stat().st_mtime > LANE_FRESH_S:
                continue                      # cheap pre-filter on the file time
        except OSError:
            continue
        st = read_state(e.path)
        aid, ctx, limit, upd = st.get("agent_id"), st.get("last_ctx"), st.get("limit"), st.get("updated")
        if (not isinstance(aid, str) or not isinstance(ctx, int) or not isinstance(limit, int) or not limit
                or not isinstance(upd, (int, float)) or now - upd > LANE_FRESH_S):
            continue
        levels = list(alerted.get(aid) or [])
        want = []
        over, hard = "over:%d" % limit, "hard:%d" % limit   # per limit: a raised limit is announced again
        if ctx >= limit and over not in levels:
            want.append(over)
        if isinstance(st.get("hard"), dict) and hard not in levels:
            want.append(hard)
        if not want:
            continue
        name = lane_name(inp.get("transcript_path") or "", aid, st.get("agent_type"))
        new_limit = -(-(max(ctx, limit) + 30_000) // 10_000) * 10_000
        cmd = ('%s --config %s set --agent %s --limit %d --for 60m --reason "..."'
               % (ctl_command(), shlex.quote(cfg.get("_path") or os.path.join(cfg.get("_dir") or "",
                                                                             "context_guard.json")),
                  shlex.quote(aid), new_limit))
        tpl = LANE_HARD if hard in want else LANE_OVER
        out.append(tpl.format(name=name, k=ctx // 1000, f=limit // 1000, cmd=cmd))
        alerted[aid] = (levels + want)[-8:]
    if out:
        state["lane_alerts"] = dict(list(alerted.items())[-LANE_ALERTS_KEPT:])
    return out


def ctl_command() -> str:
    """How the coordinator calls the control command. From the plugin it is `context-guard-ctl`
    (on the Bash tool's PATH): the plugin's own folder changes with every update."""
    here = os.path.dirname(os.path.realpath(__file__))
    root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if root and (here + os.sep).startswith(os.path.realpath(root) + os.sep):
        return "context-guard-ctl"
    return "python3 " + shlex.quote(os.path.join(here, "context_guard_ctl.py"))


def lane_name(main_tp: str, aid: str, agent_type) -> str:
    """'"<description>" (<id8>)' from the sub-agent's meta file if readable, else its agent type."""
    desc = None
    if main_tp.endswith(".jsonl"):
        try:
            with open(os.path.join(main_tp[:-len(".jsonl")], "subagents", f"agent-{aid}.meta.json"), "rb") as f:
                m = json.loads(f.read(CONFIG_MAX_BYTES))
            if isinstance(m, dict) and isinstance(m.get("description"), str) and m["description"].strip():
                desc = m["description"].strip()[:80]
        except Exception:
            pass
    return '"%s" (%s)' % (desc or agent_type or "sub-agent", aid[:8])


def pre_tool_use(inp: dict, cfg: dict) -> str:
    """'' (no opinion, normal permission flow) or a deny for an over-long foreground Bash wait."""
    if not is_bash(inp.get("tool_name")):
        return ""
    if not inp.get("agent_id") and not cfg["wait_cap_applies_to_coordinator"]:
        return ""
    if inp.get("agent_type") in cfg["skip_agent_types"]:
        return ""
    ti = inp.get("tool_input")
    if not isinstance(ti, dict) or _true(ti.get("run_in_background")):
        return ""
    t = _num(ti.get("timeout"))
    cap = cfg["wait_cap_ms"]
    if t is None or cap <= 0 or not t > cap:
        return ""
    return json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny",
        "permissionDecisionReason": DENY.format(s=cap // 1000, ms=cap)}})


def bg_job_id(inp: dict):
    """Id of a background job this tool call started, else None."""
    tr = inp.get("tool_response")
    if isinstance(tr, dict) and tr.get("backgroundTaskId"):
        return str(tr["backgroundTaskId"])
    ti = inp.get("tool_input")
    if is_bash(inp.get("tool_name")) and isinstance(ti, dict) and _true(ti.get("run_in_background")):
        return "tu:" + str(inp.get("tool_use_id") or "")
    return None


def agent_transcript(inp: dict):
    """The calling agent's own transcript: the session file, or its sub-agent file."""
    tp = inp.get("transcript_path") or ""
    aid = inp.get("agent_id")
    if not aid:
        return tp or None
    name = f"agent-{aid}.jsonl"
    if os.path.basename(tp) == name:
        return tp
    if tp.endswith(".jsonl"):
        return os.path.join(tp[:-len(".jsonl")], "subagents", name)
    return None


def read_tail(path: str, nbytes: int) -> list:
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        start = max(0, size - nbytes)
        f.seek(start)
        data = f.read()
    lines = data.split(b"\n")
    if start > 0:
        lines = lines[1:]                     # the first line is cut
    return [ln.decode("utf-8", "replace") for ln in lines if ln.strip()]


def scan(lines: list, tool_use_id):
    """-> (prompt tokens of the last real assistant message or None, timestamp of the tool_use or None)."""
    ctx, tu_ts = None, None
    for ln in reversed(lines):
        if ctx is not None and (tu_ts is not None or not tool_use_id):
            break
        if '"assistant"' not in ln:
            continue
        try:
            o = json.loads(ln)
        except ValueError:
            continue
        if o.get("type") != "assistant":
            continue
        m = o.get("message") or {}
        if tool_use_id and tu_ts is None and tool_use_id in ln:
            for c in m.get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_use" and c.get("id") == tool_use_id:
                    tu_ts = o.get("timestamp")
        u = m.get("usage")
        if ctx is None and u and m.get("model") != "<synthetic>":
            ctx = (int(u.get("input_tokens") or 0) + int(u.get("cache_read_input_tokens") or 0)
                   + int(u.get("cache_creation_input_tokens") or 0))
    return ctx, tu_ts


def iso_to_epoch(ts):
    from datetime import datetime
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


def first_line_contains(path: str, needle: str) -> bool:
    with open(path, "rb") as f:
        head = f.read(64 * 1024)
    return needle.encode() in head.split(b"\n", 1)[0]


def decide(state: dict, ctx, is_sub: bool, th, wait_s, now: float, cfg: dict, bg_job=None,
           report=False) -> list:
    """Pure decision step: updates `state` in place and returns the notices to emit.
    `th` is (note, wrapup, limit) for a sub-agent and the compaction point for the main session.
    `report`: the sub-agent's deliverable is a report file, so its notices use the report wording."""
    out = []
    fired = set(state.get("fired", []))
    notes = cfg["notes_hint"]
    if report:
        T_LIMIT, T_WRAPUP, T_NOTE, T_REPEAT = R_LIMIT, R_WRAPUP, R_NOTE, R_REPEAT
        T_HARD_START, T_HARD_LEFT, T_BG = R_HARD_START, R_HARD_LEFT, R_BG
    else:
        T_LIMIT, T_WRAPUP, T_NOTE, T_REPEAT = LIMIT, WRAPUP, NOTE, REPEAT
        T_HARD_START, T_HARD_LEFT, T_BG = HARD_START, HARD_LEFT, BG
    if is_sub:
        old, new = state.get("limit"), th[2]
        if old is not None and old != new:
            fired = set()                     # new limit: its notices fire afresh
            state.pop("stop_ctx", None)
            if new:
                out.append(CHANGED.format(o=old // 1000, f=new // 1000, w=th[1] // 1000))
        if old != new:
            state["limit"] = new              # the first call records the limit without a notice
    changed = bool(out)
    if ctx is not None:
        if is_sub:
            note, wrapup, limit = th
            ths = sorted({t for t in (note, wrapup, limit) if t}) if limit else []
        else:
            ths = [th] if th else []
        for t in list(fired):
            if ctx < t - cfg["rearm_margin"]:
                fired.discard(t)              # the context fell (compaction): re-arm
        crossed = [t for t in ths if ctx >= t and t not in fired]
        if crossed:
            top = max(crossed)
            k = ctx // 1000
            if not is_sub:
                out.append(MAIN.format(k=k))
            elif top == limit:
                out.append(T_LIMIT.format(k=k, f=limit // 1000, notes=notes))
                state["stop_ctx"] = ctx
            elif top == wrapup:
                out.append(T_WRAPUP.format(k=k, f=limit // 1000, m=(limit - ctx) // 1000, notes=notes))
            else:
                out.append(T_NOTE.format(k=k, f=limit // 1000, notes=notes))
            fired.update(t for t in ths if ctx >= t)   # a jump past several fires only the top one
        state["fired"] = sorted(fired)
        if is_sub and limit:
            if limit not in fired:
                state.pop("stop_ctx", None)   # below the limit again (or a new limit): no repeats
            hard = hard_point(cfg, limit)
            if hard and ctx >= hard:
                h = state.get("hard")
                grace = cfg["hard_stop_grace_calls"]
                if not isinstance(h, dict) or h.get("limit") != limit:
                    state["hard"] = {"limit": limit, "used": 0}
                    out.append(T_HARD_START.format(k=ctx // 1000, f=limit // 1000,
                                                 m=cfg["hard_stop_margin"] // 1000, n=grace))
                else:
                    h["used"] = min(int(h.get("used") or 0) + 1, grace + 1)
                    out.append(T_HARD_LEFT.format(n=max(grace - h["used"], 0)))
                state["stop_ctx"] = ctx
            else:
                state.pop("hard", None)       # the context fell or the limit was raised: lifted
                every = cfg["stop_repeat_every"]
                sc = state.get("stop_ctx")
                if every > 0 and isinstance(sc, int) and ctx >= sc + every and ctx >= limit:
                    out.append(T_REPEAT.format(
                        k=ctx // 1000, p=(ctx - limit) // 1000, f=limit // 1000, notes=notes,
                        h=REPEAT_HARD.format(h=hard // 1000) if hard else ""))
                    state["stop_ctx"] = ctx
        elif is_sub:
            state.pop("hard", None)
            state.pop("stop_ctx", None)
        if len(out) > changed:
            state["ctx"] = ctx
            if is_sub:
                state["th"] = {"note": note, "wrapup": wrapup, "limit": limit}
    if (wait_s is not None and cfg["slow_call_s"] > 0 and wait_s > cfg["slow_call_s"]
            and now - state.get("wait_ts", 0) >= cfg["slow_call_cooldown_s"]):
        out.append(SLOW.format(s=round(wait_s)))
        state["wait_ts"] = now
    if is_sub and bg_job:
        jobs = state.get("bg_jobs") or []
        if bg_job not in jobs:
            if now - state.get("bg_ts", 0) >= cfg["bg_reminder_cooldown_s"]:
                out.append(T_BG)
                state["bg_ts"] = now
            state["bg_jobs"] = (jobs + [bg_job])[-BG_JOBS_KEPT:]
    return out


def append_line(path: str, record: dict) -> None:
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:
        pass


def run(inp: dict, a: SimpleNamespace, cfg: dict, now=None) -> str:
    """PostToolUse: returns the hook's stdout ('' for no notice)."""
    t_start = time.time()
    now = t_start if now is None else now
    if inp.get("hook_event_name") not in (None, "PostToolUse"):
        return ""
    is_sub = bool(inp.get("agent_id"))
    if inp.get("agent_type") in cfg["skip_agent_types"]:
        return ""
    path = agent_transcript(inp)
    have = bool(path) and os.path.isfile(path)
    if a.only_if_prompt_contains and not (have and first_line_contains(path, a.only_if_prompt_contains)):
        return ""
    bg_job = bg_job_id(inp) if is_sub else None
    if not have and not bg_job:
        return ""
    tuid = inp.get("tool_use_id")
    ctx, tu_ts = None, None
    if have:
        ctx, tu_ts = scan(read_tail(path, TAIL_BYTES), tuid)
        if ctx is None and os.path.getsize(path) > TAIL_BYTES:
            ctx, tu_ts = scan(read_tail(path, TAIL_MAX), tuid)
    wait_s = None
    if isinstance(inp.get("duration_ms"), (int, float)) and not isinstance(inp.get("duration_ms"), bool):
        wait_s = inp["duration_ms"] / 1000.0
    elif tu_ts:
        t0 = iso_to_epoch(tu_ts)
        wait_s = (now - t0) if t0 else None

    ovs = load_overrides(cfg, now)
    label, source = None, "config"
    if is_sub:
        note, wrapup, limit, label, source = resolve(cfg, ovs, inp.get("agent_id"), inp.get("agent_type"))
        th = (note, wrapup, limit)
    else:
        th = compact_point(cfg, ovs)
        source = "config" if th == cfg["coordinator_compact_at"] else "coordinator"

    state_dir = a.state_dir or _path(cfg, cfg["state_dir"])
    os.makedirs(state_dir, exist_ok=True)
    key = state_key(inp, a.tag)
    sp = os.path.join(state_dir, key + ".json")
    with open(sp + ".lock", "a") as lk:
        try:
            import fcntl
            fcntl.flock(lk, fcntl.LOCK_EX)    # parallel tool calls of one agent: fire once
        except ImportError:
            pass                              # no fcntl on this platform: run without the lock
        state = read_state(sp)
        seen = state.get("tuids")
        seen = seen if isinstance(seen, list) else []
        if tuid and (tuid in seen or state.get("last_tuid") == tuid):
            return ""                         # a second registration ran for this call: already done
        notes = decide(state, ctx, is_sub, th, wait_s, now, cfg, bg_job,
                       report=is_sub and is_report(cfg, inp.get("agent_type")))
        if not is_sub:
            notes += lane_alerts(inp, state, state_dir, key, cfg, now)
        # recorded on every call: what the PreToolUse hard stop and the coordinator's lane alerts read
        state["agent_type"] = inp.get("agent_type") or ("" if is_sub else "main")
        state["updated"] = now
        if is_sub:
            state["agent_id"] = str(inp.get("agent_id"))
        if ctx is not None:
            state["last_ctx"] = ctx
        if tuid:
            state["tuids"] = (seen + [tuid])[-TUIDS_KEPT:]   # several: parallel calls interleave
            state.pop("last_tuid", None)
        tmp = f"{sp}.{os.getpid()}.tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, sp)
    if a.trace:
        append_line(a.trace, {"ts": round(now, 3), "key": key, "agent_type": inp.get("agent_type"),
                              "tool": inp.get("tool_name"), "ctx": ctx, "wait_s": wait_s,
                              "ms": round((time.time() - t_start) * 1000, 1), "notes": len(notes)})
    if not notes:
        return ""
    append_line(a.log or _path(cfg, cfg["log"]),
                {"ts": now, "key": key, "agent_type": inp.get("agent_type"), "ctx": ctx, "wait_s": wait_s,
                 "tool": inp.get("tool_name"), "source": source, "label": label,
                 "th": state.get("th"), "notes": notes})
    prune(state_dir, now)
    text = " | ".join("[context-guard] " + n for n in notes)
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text}})


def prune(state_dir: str, now: float) -> None:
    for n in os.listdir(state_dir):
        p = os.path.join(state_dir, n)
        try:
            if now - os.path.getmtime(p) > STATE_TTL_S:
                os.remove(p)
        except OSError:
            pass


def main(argv=None) -> int:
    try:
        inp = json.load(sys.stdin)
        if not isinstance(inp, dict):
            return 0
        a = parse_args(sys.argv[1:] if argv is None else argv)
        cp = config_path(a.config)
        if a.require_config and not os.path.isfile(cp):
            return 0                          # installed for all projects; this one has not opted in
        cfg = load_config(cp)
        if inp.get("hook_event_name") == "PreToolUse":
            out = pre_tool_use(inp, cfg)      # decided from stdin and the config: no transcript, no state
            rec = None
            if out:
                rec = {"ts": time.time(), "event": "PreToolUse", "deny": True,
                       "agent_id": inp.get("agent_id"), "agent_type": inp.get("agent_type"),
                       "timeout": inp["tool_input"].get("timeout")}
            else:
                out, rec = hard_stop_pre(inp, a, cfg)
            if out:
                sys.stdout.write(out + "\n")
                sys.stdout.flush()
                append_line(a.log or _path(cfg, cfg["log"]), rec)
            return 0
        out = run(inp, a, cfg)
        if out:
            sys.stdout.write(out + "\n")
    except BaseException:                     # never fail or block the tool call
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
