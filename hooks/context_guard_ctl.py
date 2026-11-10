#!/usr/bin/env python3
"""context_guard_ctl -- the coordinator's control for the context guard's limits.

The guard reads its files on every tool call, so a change made here takes effect at the next
tool call of every running agent, and an agent whose limit changed is told so by the guard.

  context_guard_ctl.py show
  context_guard_ctl.py set --default --limit 160000 --for 3h --reason "wave 3: long refactors"
  context_guard_ctl.py set --type batch-worker --limit 80000
  context_guard_ctl.py set --agent <agent id> --limit 200000 --note 120000 --label lane-x --for 90m
  context_guard_ctl.py set --coordinator --limit 250000 --for 30m --reason "finish the report chain"
  context_guard_ctl.py clear --agent <agent id>        (also: --type T, --default, --coordinator,
                                                        --expired, --all)

Scopes, most specific first: one agent (the id the Agent tool returned), one agent type, all
lanes (--default), and the coordinator's own compaction point (--coordinator). Without --for or
--until an entry stays until it is cleared. Entries go to the overrides file named in the
guard's config (context_guard.overrides.json next to it by default); the base config is never
edited. Every change is appended to context_guard.changes.jsonl beside it.

Options: --config PATH. Without it: $CONTEXT_GUARD_CONFIG, then the first
.claude/hooks/context_guard.json walking up from $CLAUDE_PROJECT_DIR, then from the current
folder, then context_guard.json next to this script. Python 3.8+, standard library only.
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import context_guard as cg  # noqa: E402

UNITS = {"m": 60, "h": 3600, "d": 86400}


def parse_for(text):
    m = re.fullmatch(r"(\d+)([mhd])", text.strip())
    if not m:
        raise SystemExit("--for takes a number and a unit, e.g. 45m, 3h or 1d")
    return int(m.group(1)) * UNITS[m.group(2)]


def parse_until(text):
    try:
        t = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise SystemExit("--until takes an ISO time with a UTC offset, e.g. 2030-01-31T18:00:00+01:00")
    if t.tzinfo is None:
        raise SystemExit("--until needs a UTC offset (a time without one is ambiguous)")
    return t


def read_raw(path):
    try:
        with open(path) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except FileNotFoundError:
        return {}
    except ValueError:
        raise SystemExit(f"{path} is not valid JSON; fix or remove it first")


def write_raw(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def slot(args):
    """(group, key) of the entry the command addresses; group None = top level."""
    chosen = [bool(args.default), bool(args.type), bool(args.agent), bool(args.coordinator)]
    if sum(chosen) != 1:
        raise SystemExit("name exactly one scope: --default, --type T, --agent ID or --coordinator")
    if args.default:
        return None, "default"
    if args.coordinator:
        return None, "coordinator"
    return ("types", args.type) if args.type else ("agents", args.agent)


def get_entry(data, group, key):
    return (data.get(group) or {}).get(key) if group else data.get(key)


def put_entry(data, group, key, entry):
    if group:
        data.setdefault(group, {})
        if entry is None:
            data[group].pop(key, None)
            if not data[group]:
                del data[group]
        else:
            data[group][key] = entry
    elif entry is None:
        data.pop(key, None)
    else:
        data[key] = entry


def remaining(entry, now):
    until = entry.get("until")
    if until is None:
        return "until cleared"
    try:
        left = datetime.fromisoformat(str(until).replace("Z", "+00:00")).timestamp() - now
    except ValueError:
        return "bad until: ignored"
    if left <= 0:
        return "EXPIRED"
    return "%dh %02dm left" % (left // 3600, (left % 3600) // 60)


def describe(entry):
    parts = []
    for k in ("limit", "compact_at", "wrapup", "note"):
        if entry.get(k) is not None:
            parts.append("%s %dk" % (k, entry[k] // 1000) if isinstance(entry[k], int) else f"{k} {entry[k]!r}")
    for k in ("label", "reason"):
        if entry.get(k):
            parts.append(f"{k}: {entry[k]}")
    return ", ".join(parts)


def cmd_show(cfg, path, now):
    n, w, lim = cg.limits_for(cfg, None)
    print("base config (%s)" % cfg["_path"])
    print("  lanes: limit %dk, wrap-up from %dk" % (lim // 1000, w // 1000) if lim else "  lanes: no context notices")
    for t, e in sorted(cfg["agent_types"].items()):
        _, tw, tl = cg.limits_for(cfg, t)
        print("  type %-16s limit %dk, wrap-up from %dk%s" % (t, tl // 1000, tw // 1000,
                                                              ", deliverable report" if cg.is_report(cfg, t) else ""))
    print("  coordinator: compaction notice at %dk" % (cfg["coordinator_compact_at"] // 1000)
          if cfg["coordinator_compact_at"] else "  coordinator: no compaction notice")
    data = read_raw(path)
    rows = []
    for key in ("default", "coordinator"):
        if isinstance(data.get(key), dict):
            rows.append((key, data[key]))
    for group in ("types", "agents"):
        for key, e in sorted((data.get(group) or {}).items()):
            if isinstance(e, dict):
                rows.append((f"{group[:-1]} {key}", e))
    print("overrides (%s)" % path)
    if not rows:
        print("  none")
    for name, e in rows:
        print("  %-28s %s  [%s]" % (name, describe(e), remaining(e, now)))
    ovs = cg.load_overrides(cfg, now)
    _, w, lim, _, src = cg.resolve(cfg, ovs, None, None)
    print("in effect now: lanes limit %dk, wrap-up from %dk (%s); coordinator compaction notice at %dk"
          % (lim // 1000, w // 1000, src, cg.compact_point(cfg, ovs) // 1000))


def cmd_set(args, cfg, path, now):
    group, key = slot(args)
    if args.limit is None or args.limit < 0:
        raise SystemExit("set needs --limit N (tokens)")
    if key == "coordinator":
        if args.wrapup is not None or args.note is not None:
            raise SystemExit("--coordinator takes --limit only (its compaction point)")
        entry = {"compact_at": args.limit}
    else:
        entry = {"limit": args.limit}
        if args.wrapup is not None:
            entry["wrapup"] = args.wrapup
        if args.note is not None:
            entry["note"] = args.note
        if args.label:
            entry["label"] = args.label
        if cg._limit_entry(entry, cfg["wrapup_margin"]) is None:
            raise SystemExit("not valid: limit must be above 0 and wrapup must not exceed it")
    if args.for_ and args.until:
        raise SystemExit("give --for or --until, not both")
    if args.for_:
        until = datetime.fromtimestamp(now + parse_for(args.for_), timezone.utc).astimezone()
        entry["until"] = until.isoformat(timespec="seconds")
    elif args.until:
        entry["until"] = parse_until(args.until).isoformat(timespec="seconds")
    if args.reason:
        entry["reason"] = args.reason
    data = read_raw(path)
    old = get_entry(data, group, key)
    put_entry(data, group, key, entry)
    write_raw(path, data)
    record(path, now, "set", group, key, old, entry)
    print("set %s: %s  [%s]" % (key if not group else f"{group[:-1]} {key}", describe(entry), remaining(entry, now)))
    print("In force at the next tool call of each affected agent; the guard tells an agent whose limit changed.")


def cmd_clear(args, path, now):
    data = read_raw(path)
    if args.all:
        removed = [k for k in data]
        data = {}
    elif args.expired:
        removed = []
        for key in ("default", "coordinator"):
            if isinstance(data.get(key), dict) and remaining(data[key], now) == "EXPIRED":
                removed.append(key)
                del data[key]
        for group in ("types", "agents"):
            for key in [k for k, e in (data.get(group) or {}).items()
                        if isinstance(e, dict) and remaining(e, now) == "EXPIRED"]:
                removed.append(f"{group[:-1]} {key}")
                put_entry(data, group, key, None)
    else:
        group, key = slot(args)
        old = get_entry(data, group, key)
        removed = [key if not group else f"{group[:-1]} {key}"] if old is not None else []
        put_entry(data, group, key, None)
    write_raw(path, data)
    record(path, now, "clear", None, ", ".join(removed) or "nothing", None, None)
    print("cleared: " + (", ".join(removed) or "nothing to clear"))


def record(path, now, action, group, key, old, new):
    cg.append_line(os.path.join(os.path.dirname(path), "context_guard.changes.jsonl"),
                   {"ts": datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds"),
                    "action": action, "scope": key if not group else f"{group[:-1]} {key}",
                    "old": old, "new": new})


def main(argv=None):
    p = argparse.ArgumentParser(description="Show or change the context guard's limits.")
    p.add_argument("command", choices=["show", "set", "clear"])
    p.add_argument("--config", default="")
    p.add_argument("--default", action="store_true", help="all lanes")
    p.add_argument("--type", help="one agent type")
    p.add_argument("--agent", help="one agent, by the id the Agent tool returned")
    p.add_argument("--coordinator", action="store_true", help="the coordinator's compaction point")
    p.add_argument("--limit", type=int)
    p.add_argument("--wrapup", type=int)
    p.add_argument("--note", type=int)
    p.add_argument("--label")
    p.add_argument("--for", dest="for_", help="how long the entry holds: 45m, 3h, 1d")
    p.add_argument("--until", help="ISO time with a UTC offset")
    p.add_argument("--reason")
    p.add_argument("--expired", action="store_true")
    p.add_argument("--all", action="store_true")
    args = p.parse_args(argv)
    cfg = cg.load_config(cg.config_path(args.config, (os.getcwd(),)))
    path = cg._path(cfg, cfg["overrides_file"])
    if not path:
        raise SystemExit('the config sets "overrides_file" to "": name a file there first')
    now = time.time()
    if args.command == "show":
        cmd_show(cfg, path, now)
    elif args.command == "set":
        cmd_set(args, cfg, path, now)
    else:
        cmd_clear(args, path, now)
    return 0


if __name__ == "__main__":
    sys.exit(main())
