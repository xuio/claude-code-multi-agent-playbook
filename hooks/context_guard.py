#!/usr/bin/env python3
"""context_guard -- Claude Code hook: context-size notices, a foreground wait cap, cache reminders.

Register the same command for two hook events (see settings.example.json).

PreToolUse (Bash): denies a foreground Bash call whose `timeout` is above the wait cap (default
270 s). Such a call outlives the 5-minute prompt cache, so the agent's next call would write its
whole context again. Sub-agents only, unless the config says otherwise.

PostToolUse (all tools): puts a one-line "[context-guard] ..." notice next to the tool result when
  - a sub-agent comes within the wrap-up margin of its context limit, and again at the limit;
  - the coordinator (the main session) reaches its compaction point;
  - a tool call took longer than the prompt cache lives;
  - a sub-agent started a background job (the notice says how to wait for it).

Agents cannot see their own context size; this hook tells them. A context notice fires once per
(session, agent) and re-arms after the context fell again (compaction).

Limits come from two files next to this script, both read on every call, so a change takes
effect at the next tool call of every running agent:
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
import sys
import time
from types import SimpleNamespace

DEFAULTS = {
    "lane_limit": 120_000,              # sub-agent: stop here (0 = no context notices for sub-agents)
    "wrapup_margin": 30_000,            # sub-agent: wrap-up notice this many tokens before the limit
    "coordinator_compact_at": 190_000,  # main session: compaction notice (0 = off)
    "agent_types": {},                  # {"<agent type>": {"limit": int, "wrapup_margin": int}}
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

FLAGS = ("--config", "--state-dir", "--log", "--trace", "--tag", "--only-if-prompt-contains")


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
    i = 0
    while i < len(argv):
        if argv[i] not in FLAGS or i + 1 >= len(argv):
            raise ValueError("bad argument: " + argv[i])
        a[argv[i][2:].replace("-", "_")] = argv[i + 1]
        i += 2
    return SimpleNamespace(**a)


def config_path(arg: str) -> str:
    return (arg or os.environ.get("CONTEXT_GUARD_CONFIG")
            or os.path.join(os.path.dirname(os.path.abspath(__file__)), "context_guard.json"))


def load_config(path: str) -> dict:
    """Defaults, overlaid with the well-typed keys of the config file. Any problem: defaults."""
    cfg = dict(DEFAULTS)
    cfg["_dir"] = os.path.dirname(os.path.abspath(path)) if path else ""
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


def decide(state: dict, ctx, is_sub: bool, th, wait_s, now: float, cfg: dict, bg_job=None) -> list:
    """Pure decision step: updates `state` in place and returns the notices to emit.
    `th` is (note, wrapup, limit) for a sub-agent and the compaction point for the main session."""
    out = []
    fired = set(state.get("fired", []))
    notes = cfg["notes_hint"]
    if is_sub:
        old, new = state.get("limit"), th[2]
        if old is not None and old != new:
            fired = set()                     # new limit: its notices fire afresh
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
                out.append(LIMIT.format(k=k, f=limit // 1000, notes=notes))
            elif top == wrapup:
                out.append(WRAPUP.format(k=k, f=limit // 1000, m=(limit - ctx) // 1000, notes=notes))
            else:
                out.append(NOTE.format(k=k, f=limit // 1000, notes=notes))
            fired.update(t for t in ths if ctx >= t)   # a jump past several fires only the top one
        state["fired"] = sorted(fired)
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
                out.append(BG)
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
    key = "".join(ch if ch.isalnum() or ch in "-_" else "_"
                  for ch in f"{inp.get('session_id', 'nosession')}_{inp.get('agent_id') or 'main'}{a.tag}")
    sp = os.path.join(state_dir, key + ".json")
    with open(sp + ".lock", "a") as lk:
        try:
            import fcntl
            fcntl.flock(lk, fcntl.LOCK_EX)    # parallel tool calls of one agent: fire once
        except ImportError:
            pass                              # no fcntl on this platform: run without the lock
        try:
            with open(sp) as f:
                state = json.load(f)
        except (OSError, ValueError):
            state = {}
        before = json.dumps(state, sort_keys=True)
        notes = decide(state, ctx, is_sub, th, wait_s, now, cfg, bg_job)
        if json.dumps(state, sort_keys=True) != before:
            state["agent_type"] = inp.get("agent_type") or ("" if is_sub else "main")
            state["updated"] = now
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
        cfg = load_config(config_path(a.config))
        if inp.get("hook_event_name") == "PreToolUse":
            out = pre_tool_use(inp, cfg)      # decided from stdin and the config: no transcript, no state
            if out:
                sys.stdout.write(out + "\n")
                sys.stdout.flush()
                append_line(a.log or _path(cfg, cfg["log"]),
                            {"ts": time.time(), "event": "PreToolUse", "deny": True,
                             "agent_id": inp.get("agent_id"), "agent_type": inp.get("agent_type"),
                             "timeout": inp["tool_input"].get("timeout")})
            return 0
        out = run(inp, a, cfg)
        if out:
            sys.stdout.write(out + "\n")
    except BaseException:                     # never fail or block the tool call
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
