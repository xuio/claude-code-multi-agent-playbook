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

One sub-agent in detail ("did it do anything since I closed it?"):
  agent_scan.py --agent a1b2c3d4      one block: type, description, state, context and limit,
                                      first and last activity, tool calls, its last text
  agent_scan.py --agent a1b2 --since 14:30
                                      adds one line: its activity since then. --since takes HH:MM
                                      (today, or yesterday if that is still to come), an ISO date-time,
                                      or 90m / 2h (that long ago). The line names the date and time
                                      it used, so a time read as yesterday shows
  The id may be a unique prefix. With --json the block is one JSON object.

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


def parse_ts(ts):
    """A transcript timestamp as a time with an offset (one without an offset is UTC), or None."""
    try:
        t = datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo is not None else t.replace(tzinfo=datetime.timezone.utc)


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
            t = parse_ts(ts)
            if t is None:
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


def agent_state(meta, r, now):
    """running, ended or stale: from the sub-agent's meta file and its transcript scan."""
    idle_m = (now - r["last"]).total_seconds() / 60 if r["last"] else 0.0
    if meta.get("finished") is True or (meta.get("finished") is None and r["stop"] == "end_turn"):
        return "ended"
    return "stale" if idle_m > STALE_MIN else "running"


def read_meta(path):
    """The sub-agent's .meta.json beside its transcript, or {}."""
    try:
        with open(path[:-len(".jsonl")] + ".meta.json") as f:
            meta = json.load(f)
        return meta if isinstance(meta, dict) else {}
    except (OSError, ValueError):
        return {}


def sub_agents(session):
    """[(agent id, transcript path)] of the session's sub-agents."""
    sub_dir = os.path.join(session[:-len(".jsonl")], "subagents")
    return [(os.path.basename(p)[len("agent-"):-len(".jsonl")], p)
            for p in sorted(glob.glob(os.path.join(sub_dir, "agent-*.jsonl")))]


def parse_since(text, now):
    """The UTC time that `--since` names: HH:MM (local, today; yesterday if still to come), an ISO
    date-time (local if it has no offset), or <N>m / <N>h ago. None if it cannot be read."""
    text = str(text).strip()
    m = re.fullmatch(r"(\d+)([mh])", text)
    if m:
        return now - datetime.timedelta(minutes=int(m.group(1)) * (60 if m.group(2) == "h" else 1))
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if m:
        if int(m.group(1)) > 23 or int(m.group(2)) > 59:
            return None
        local = now.astimezone()
        t = local.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
        if t > local:
            t -= datetime.timedelta(days=1)
        return t.astimezone(datetime.timezone.utc)
    try:
        t = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.astimezone()                    # no offset: local time
    return t.astimezone(datetime.timezone.utc)


def activity(path, since):
    """Tool calls, the last assistant text, and (with `since`) the activity from then on. Reads the
    transcript line by line, as scan() does; keeps only the last text."""
    r = {"tool_calls": 0, "last_text": "", "since_messages": 0, "since_calls": 0, "since_last": None}
    seen = set()
    with open(path, errors="replace") as f:
        for line in f:
            if '"assistant"' not in line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            m = d.get("message") or {}
            if d.get("type") != "assistant" or m.get("model") == "<synthetic>":
                continue
            content = m.get("content") if isinstance(m.get("content"), list) else []
            calls = sum(1 for b in content if isinstance(b, dict) and b.get("type") == "tool_use")
            r["tool_calls"] += calls
            texts = [b.get("text") for b in content
                     if isinstance(b, dict) and b.get("type") == "text" and str(b.get("text") or "").strip()]
            if texts:
                r["last_text"] = " ".join(str(x) for x in texts)
            if since is None:
                continue
            t = parse_ts(d.get("timestamp"))
            if t is None or t < since:
                continue
            mid = m.get("id")
            if mid is None or mid not in seen:
                seen.add(mid)
                r["since_messages"] += 1
            r["since_calls"] += calls
            r["since_last"] = t
    return r


def _local(t, fmt="%Y-%m-%d %H:%M"):
    return t.astimezone().strftime(fmt) if t else None


def agent_detail(session, cfg, now, prefix, since=None, since_text=None):
    """(detail dict, None) for the one sub-agent whose id starts with `prefix`, or (None, error text)."""
    found = [(aid, p) for aid, p in sub_agents(session) if aid.startswith(prefix)]
    exact = [(aid, p) for aid, p in found if aid == prefix]
    found = exact or found
    if not found:
        return None, "no sub-agent with id %s in %s" % (prefix, session)
    if len(found) > 1:
        lines = ["%d sub-agents match %s:" % (len(found), prefix)]
        for aid, p in found:
            meta = read_meta(p)
            lines.append("  %s  %s  %s" % (aid, meta.get("agentType") or "?", meta.get("description") or ""))
        return None, "\n".join(lines)
    aid, path = found[0]
    meta = read_meta(path)
    r = scan(path, now)
    a = activity(path, since)
    kind = str(meta.get("agentType") or "?")
    limit, _ = limit_for(cfg, kind, False, aid)
    text = " ".join(a["last_text"].split())
    out = {"id": aid, "type": kind, "description": str(meta.get("description") or ""),
           "state": agent_state(meta, r, now), "ctx": r["ctx"], "limit": limit, "peak": r["peak"],
           "first": _local(r["first"]), "last": _local(r["last"]),
           "idle_min": round((now - r["last"]).total_seconds() / 60, 1) if r["last"] else None,
           "tool_calls": a["tool_calls"], "last_text": text[:300]}
    if since is not None:
        out["since"] = {"when": since_text, "from": _local(since), "assistant_messages": a["since_messages"],
                        "tool_calls": a["since_calls"], "last": _local(a["since_last"])}
    return out, None


def fmt_detail(d):
    k = lambda n: "%dk" % round(n / 1000)     # noqa: E731
    lines = ["agent %s  %s  %s" % (d["id"], d["type"], d["description"]),
             "  state      %s" % d["state"],
             "  context    %s of %s (peak %s)" % (k(d["ctx"]), k(d["limit"]) if d["limit"] else "no limit",
                                                  k(d["peak"])),
             "  first      %s" % (d["first"] or "-"),
             "  last       %s (%s min ago)" % (d["last"], d["idle_min"]) if d["last"] else "  last       -",
             "  tool calls %d" % d["tool_calls"],
             "  last text  %s" % (d["last_text"] or "-")]
    s = d.get("since")
    if s is not None:                         # the resolved date and time, not the text that was typed
        if s["assistant_messages"] or s["tool_calls"]:
            lines.append("  since %s: %d assistant messages, %d tool calls, last at %s"
                         % (s["from"], s["assistant_messages"], s["tool_calls"], s["last"]))
        else:
            lines.append("  since %s: no activity" % s["from"])
    return "\n".join(lines)


def rows(session, cfg, now, show_all):
    out = []
    r = scan(session, now)
    limit, wrapup = limit_for(cfg, None, True)
    out.append(describe("coordinator", "main", "running", r, limit, wrapup, now))
    sub_dir = os.path.join(session[:-len(".jsonl")], "subagents")
    for path in sorted(glob.glob(os.path.join(sub_dir, "agent-*.jsonl")), key=os.path.getmtime):
        aid = os.path.basename(path)[len("agent-"):-len(".jsonl")]
        meta = read_meta(path)
        r = scan(path, now)
        state = agent_state(meta, r, now)
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
    p.add_argument("--agent", metavar="ID",
                   help="one sub-agent in detail; a full id or a unique prefix (example: --agent a1b2c3d4)")
    p.add_argument("--since", metavar="WHEN",
                   help="with --agent: its activity since HH:MM, an ISO date-time, or 90m / 2h ago "
                        "(example: --since 14:30)")
    p.add_argument("--now", metavar="ISO", help=argparse.SUPPRESS)   # a fixed "now", for tests
    args = p.parse_args(argv)
    if args.since and not args.agent:
        p.error("--since needs --agent")
    session = args.session or newest_session(project_dir(args.project))
    if not session or not os.path.isfile(session):
        print("no session transcript found (looked in %s); pass --session FILE.jsonl"
              % (args.session or project_dir(args.project)), file=sys.stderr)
        return 1
    now = parse_since(args.now, datetime.datetime.now(datetime.timezone.utc)) if args.now else None
    if args.now and now is None:
        p.error("--now: not an ISO date-time")
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if args.agent:
        since = parse_since(args.since, now) if args.since else None
        if args.since and since is None:
            p.error("--since: use HH:MM, an ISO date-time, or <N>m / <N>h")
        d, err = agent_detail(session, load_limits(args.config, now), now, args.agent, since, args.since)
        if err:
            print(err, file=sys.stderr)
            return 1
        print(json.dumps(d, indent=1) if args.json else fmt_detail(d))
        return 0
    out = rows(session, load_limits(args.config, now), now, args.all)
    if args.json:
        print(json.dumps(out, indent=1))
    else:
        for row in out:
            print(fmt(row).rstrip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
