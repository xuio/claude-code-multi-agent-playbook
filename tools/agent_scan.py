#!/usr/bin/env python3
"""agent_scan -- one line per agent of a Claude Code session: context, activity, cache misses, flags.

Reads the session's transcript and its sub-agent transcripts (never prints their content) and
reports for the coordinator and every sub-agent:

  state    running, ended, or stale (no "finished" mark, but silent for more than two hours:
           its session was restarted or it was stopped)
  age      time since its first transcript line
  ctx      prompt size of its last model call against its limit, and its peak
  idle     minutes since its last transcript line
  1h       tool calls, polls and edits in the last hour
  rewrites model calls that wrote most of the context to the cache again (a cache miss: the
           agent was resumed or waited longer than the cache lives) and the tokens that cost

Flags:
  CTX      a sub-agent's context at or past its wrap-up point (limit minus the wrap-up margin)
  OVER     context at or past the limit: a sub-agent hands off now; for the coordinator the limit
           is its compaction point
  SILENT   running, but no transcript line for more than 15 minutes
  HUNG     a tool call without a result for more than 20 minutes
  NOMSG    a running sub-agent older than an hour that sent no message (SendMessage tool) in the
           last 60 minutes
  POLLING  more than 70 % of the last hour's tool calls are polls: blocked on a job
  REWRITES three or more cache misses: the agent keeps being woken or waits too long

Usage:
  agent_scan.py                       newest session of the project in the current folder
  agent_scan.py --project DIR         newest session of that project
  agent_scan.py --session FILE.jsonl  one session transcript
  options: --config .claude/hooks/context_guard.json (the context guard's config: limits per
           agent type, and the overrides file beside it that the coordinator changes at run time;
           without it 150k per lane, wrap-up 20k earlier, coordinator 180k),
           --all (also agents that ended or went stale), --json

Python 3.8+, standard library only.
"""
import argparse
import datetime
import glob
import json
import os
import re
import sys

POLL_HINTS = ("sleep", "bgwait", " wait", "tail ", "status", "logs")
STALE_MIN = 120


def project_dir(path):
    """Where Claude Code keeps the transcripts of the project at `path`."""
    slug = re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(path))
    return os.path.join(os.path.expanduser("~/.claude/projects"), slug)


def newest_session(pdir):
    files = glob.glob(os.path.join(pdir, "*.jsonl"))
    return max(files, key=os.path.getmtime) if files else None


def load_limits(path, now=None):
    """The context guard's limits: its base config, and the still-valid entries of the overrides
    file beside it (written by context_guard_ctl.py). Anything unreadable: the defaults."""
    cfg = {"lane_limit": 150_000, "wrapup_margin": 20_000, "coordinator_compact_at": 180_000, "agent_types": {},
           "overrides_file": "context_guard.overrides.json"}
    ovs = {"default": None, "types": {}, "agents": {}, "coordinator": None}
    cfg["overrides"] = ovs
    if not path:
        return cfg
    try:
        with open(path) as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = None
    for k in cfg:
        if k != "overrides" and isinstance(d, dict) and isinstance(d.get(k), type(cfg[k])) \
                and not isinstance(d.get(k), bool):
            cfg[k] = d[k]
    now = now or datetime.datetime.now(datetime.timezone.utc)
    name = os.path.expanduser(cfg["overrides_file"])
    try:
        with open(name if os.path.isabs(name) else os.path.join(os.path.dirname(os.path.abspath(path)), name)) as f:
            d = json.load(f)
    except (OSError, ValueError):
        return cfg
    if isinstance(d, dict):
        ovs["default"] = _active(d.get("default"), now)
        ovs["coordinator"] = _active(d.get("coordinator"), now)
        for group in ("types", "agents"):
            if isinstance(d.get(group), dict):
                ovs[group] = {str(k): e for k, e in d[group].items() if _active(e, now)}
    return cfg


def _active(entry, now):
    """The override entry if it has not expired (`until`: ISO time with UTC offset), else None."""
    if not isinstance(entry, dict):
        return None
    if entry.get("until") is None:
        return entry
    try:
        t = datetime.datetime.fromisoformat(str(entry["until"]).replace("Z", "+00:00"))
        return entry if t.tzinfo is not None and now < t else None
    except ValueError:
        return None


def _count(v, least=0):
    return isinstance(v, int) and not isinstance(v, bool) and v >= least


def limit_for(cfg, agent_type, is_main, agent_id=None):
    """(limit, wrap-up point). The most specific valid override wins (agent, agent type, all lanes),
    then the base config: the same order the context guard uses. A limit of 0 means "no limit"
    (no flag), as in the guard."""
    ovs = cfg["overrides"]
    if is_main:
        point = (ovs["coordinator"] or {}).get("compact_at")
        point = point if _count(point) else cfg["coordinator_compact_at"]
        return point, point
    margin = cfg["wrapup_margin"]
    for entry in (ovs["agents"].get(str(agent_id)), ovs["types"].get(agent_type), ovs["default"]):
        limit = (entry or {}).get("limit")
        if _count(limit, 1):
            wrapup = entry.get("wrapup")
            if wrapup is None:
                return limit, (limit - margin if 0 < margin < limit else limit)
            if _count(wrapup, 1) and wrapup <= limit:
                return limit, wrapup
    limit = cfg["lane_limit"]
    e = cfg["agent_types"].get(agent_type)
    if isinstance(e, dict):
        limit = e["limit"] if _count(e.get("limit")) else limit
        margin = e["wrapup_margin"] if _count(e.get("wrapup_margin")) else margin
    return limit, (limit - margin if 0 < margin < limit else limit)


def scan(path, now):
    """Facts about one transcript, from its timestamps, usage numbers and tool names only."""
    r = {"calls_1h": 0, "polls_1h": 0, "edits_1h": 0, "ctx": 0, "peak": 0, "model_calls": 0,
         "rewrites": 0, "rewrite_tokens": 0, "first": None, "last": None, "stop": None, "hung_s": 0,
         "last_msg": None}
    pending = {}                              # tool_use id -> time, until its tool_result arrives
    seen = set()                              # message ids: one message can span several lines
    with open(path, errors="replace") as f:
        for line in f:
            if '"timestamp"' not in line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            kind, ts = d.get("type"), d.get("timestamp")
            if kind not in ("assistant", "user") or not ts:
                continue
            try:
                t = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                continue
            r["first"] = r["first"] or t
            r["last"] = t
            m = d.get("message") or {}
            content = m.get("content") if isinstance(m.get("content"), list) else []
            if kind == "user":
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        pending.pop(b.get("tool_use_id"), None)
                continue
            if m.get("model") == "<synthetic>":
                continue
            u = m.get("usage") or {}
            mid = m.get("id")
            if u and mid not in seen:
                seen.add(mid)
                written = int(u.get("cache_creation_input_tokens") or 0)
                ctx = int(u.get("input_tokens") or 0) + int(u.get("cache_read_input_tokens") or 0) + written
                if ctx:
                    r["model_calls"] += 1
                    if r["model_calls"] > 1 and ctx > 20_000 and written > 0.5 * ctx:
                        r["rewrites"] += 1
                        r["rewrite_tokens"] += written
                    r["ctx"] = ctx
                    r["peak"] = max(r["peak"], ctx)
            r["stop"] = m.get("stop_reason") or r["stop"]
            for b in content:
                if not (isinstance(b, dict) and b.get("type") == "tool_use"):
                    continue
                pending[b.get("id")] = t
                name = str(b.get("name") or "")
                if "SendMessage" in name:
                    r["last_msg"] = t
                if (now - t).total_seconds() > 3600:
                    continue
                r["calls_1h"] += 1
                cmd = str((b.get("input") or {}).get("command", ""))[:80]
                if any(h in cmd for h in POLL_HINTS):
                    r["polls_1h"] += 1
                if "Edit" in name or "Write" in name or ("git" in cmd and "commit" in cmd):
                    r["edits_1h"] += 1
    if pending:
        r["hung_s"] = (now - min(pending.values())).total_seconds()
    return r


def describe(name, kind, state, r, limit, wrapup, now):
    age_h = (now - r["first"]).total_seconds() / 3600 if r["first"] else 0.0
    idle_m = (now - r["last"]).total_seconds() / 60 if r["last"] else 0.0
    ended = state != "running"
    flags = []
    if limit and r["ctx"] >= limit:
        flags.append("OVER")
    elif wrapup and r["ctx"] >= wrapup:
        flags.append("CTX")
    if not ended:
        if idle_m > 15:
            flags.append("SILENT")
        if r["hung_s"] > 1200:
            flags.append("HUNG")
        if r["calls_1h"] and r["polls_1h"] / r["calls_1h"] > 0.7:
            flags.append("POLLING")
        if kind != "main" and age_h > 1 and (r["last_msg"] is None or (now - r["last_msg"]).total_seconds() > 3600):
            flags.append("NOMSG")
    if r["rewrites"] >= 3:
        flags.append("REWRITES")
    return {"name": name, "type": kind, "state": state, "age_h": round(age_h, 1),
            "ctx": r["ctx"], "limit": limit, "peak": r["peak"], "idle_min": round(idle_m, 1),
            "calls_1h": r["calls_1h"], "polls_1h": r["polls_1h"], "edits_1h": r["edits_1h"],
            "model_calls": r["model_calls"], "rewrites": r["rewrites"], "rewrite_tokens": r["rewrite_tokens"],
            "flags": flags}


def rows(session, cfg, now, show_all):
    out = []
    r = scan(session, now)
    limit, wrapup = limit_for(cfg, None, True)
    out.append(describe("coordinator", "main", "running", r, limit, wrapup, now))
    sub_dir = os.path.join(session[:-len(".jsonl")], "subagents")
    for path in sorted(glob.glob(os.path.join(sub_dir, "agent-*.jsonl")), key=os.path.getmtime):
        aid = os.path.basename(path)[len("agent-"):-len(".jsonl")]
        meta = {}
        try:
            with open(path[:-len(".jsonl")] + ".meta.json") as f:
                meta = json.load(f)
        except (OSError, ValueError):
            pass
        r = scan(path, now)
        idle_m = (now - r["last"]).total_seconds() / 60 if r["last"] else 0.0
        if meta.get("finished") is True or (meta.get("finished") is None and r["stop"] == "end_turn"):
            state = "ended"
        else:
            state = "stale" if idle_m > STALE_MIN else "running"
        if state != "running" and not show_all:
            continue
        kind = str(meta.get("agentType") or "?")
        limit, wrapup = limit_for(cfg, kind, False, aid)
        label = str(meta.get("description") or "")[:28]
        out.append(describe(f"{aid[:9]} {label}".strip(), kind, state, r, limit, wrapup, now))
    return out


def fmt(row):
    k = lambda n: "%4.0fk" % (n / 1000)       # noqa: E731
    rewrites = "%d (%s)" % (row["rewrites"], k(row["rewrite_tokens"]).strip()) if row["rewrites"] else "0"
    return ("%-38s %-15s %-7s age %5.1fh  ctx %s/%s peak %s  idle %6.1fm  1h: calls %3d polls %3d edits %3d  "
            "rewrites %-10s %s" % (row["name"][:38], row["type"][:15], row["state"], row["age_h"], k(row["ctx"]),
                                   k(row["limit"]).strip(), k(row["peak"]).strip(), row["idle_min"],
                                   row["calls_1h"], row["polls_1h"], row["edits_1h"], rewrites,
                                   " ".join(row["flags"])))


def main(argv=None):
    p = argparse.ArgumentParser(description="One line per agent of a Claude Code session.")
    p.add_argument("--session", help="session transcript (.jsonl)")
    p.add_argument("--project", default=".", help="project folder (default: the current folder)")
    p.add_argument("--config", default="", help="the context guard's config (context_guard.json): limits and overrides")
    p.add_argument("--all", action="store_true", help="also list agents that ended")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    session = args.session or newest_session(project_dir(args.project))
    if not session or not os.path.isfile(session):
        print("no session transcript found (looked in %s); pass --session FILE.jsonl"
              % (args.session or project_dir(args.project)), file=sys.stderr)
        return 1
    now = datetime.datetime.now(datetime.timezone.utc)
    out = rows(session, load_limits(args.config, now), now, args.all)
    if args.json:
        print(json.dumps(out, indent=1))
    else:
        for row in out:
            print(fmt(row).rstrip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
