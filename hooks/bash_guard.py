#!/usr/bin/env python3
"""bash_guard -- Claude Code PreToolUse hook for Bash calls (coordinator and all sub-agents).

Rejects (exit code 2, reason on stderr, which Claude sees) a command that, at a command position
(the start, or after && || ; | & ( or a newline):
  - runs `cd` or `pushd` (rule: absolute paths and `git -C <dir>`, never cd);
  - runs a command listed under "blocked_commands" in the config with a subcommand that is not
    allowed there (typical use: heavy builds and test runs that belong on a build host or in the
    job queue, not on the user's machine).

Text inside quotes is one token, so `queue run "cargo test"` passes while `cargo test` does not.
Heredoc bodies are ignored. Any parse problem lets the command through: the guard fails open.

Config: bash_guard.json next to this script (see bash_guard.example.json), or --config PATH, or
the environment variable BASH_GUARD_CONFIG. Without a config only cd and pushd are blocked.
Python 3.8+, standard library only.
"""
import json
import os
import re
import shlex
import sys

OPS = {"&&", "||", ";", "|", "&", "(", ")", "\n", "|&", ";;"}
PREFIXES = ("env", "time", "nohup", "exec", "command", "builtin")
HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
CD_MESSAGE = "use absolute paths and `git -C <dir>`"


def load_config(path):
    """{"block_cd": bool, "cd_message": str, "blocked_commands": {name: {"allow_subcommands": [...],
    "message": str}}}. Any problem: block cd only."""
    cfg = {"block_cd": True, "cd_message": CD_MESSAGE, "blocked_commands": {}}
    try:
        with open(path) as f:
            d = json.load(f)
    except Exception:
        return cfg
    if not isinstance(d, dict):
        return cfg
    if isinstance(d.get("block_cd"), bool):
        cfg["block_cd"] = d["block_cd"]
    if isinstance(d.get("cd_message"), str) and d["cd_message"].strip():
        cfg["cd_message"] = d["cd_message"].strip()
    bc = d.get("blocked_commands")
    if isinstance(bc, dict):
        for name, entry in bc.items():
            if not isinstance(name, str) or not isinstance(entry, dict):
                continue
            allow = entry.get("allow_subcommands")
            allow = [x for x in allow if isinstance(x, str)] if isinstance(allow, list) else []
            msg = entry.get("message")
            cfg["blocked_commands"][name] = {"allow": set(allow),
                                             "message": msg.strip() if isinstance(msg, str) else ""}
    return cfg


def strip_heredocs(cmd):
    out, lines, i = [], cmd.split("\n"), 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        tags = [m.group(2) for m in HEREDOC.finditer(line)]
        i += 1
        for tag in tags:
            while i < len(lines) and lines[i].strip() != tag:
                i += 1
            i += 1
    return "\n".join(out)


def tokens(cmd):
    lex = shlex.shlex(cmd, posix=True, punctuation_chars=";&|()\n")
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    lex.commenters = "#"
    return list(lex)


def check(cmd, cfg):
    """None (allowed) or the reason for the block."""
    toks = tokens(strip_heredocs(cmd))
    blocked = cfg["blocked_commands"]
    at_start = True
    i = 0
    while i < len(toks):
        t = toks[i]
        if t in OPS or set(t) <= set(";&|()\n"):
            at_start = True
            i += 1
            continue
        if at_start:
            if ASSIGN.match(t) or t in PREFIXES:
                i += 1
                continue
            word = t.rsplit("/", 1)[-1]
            if cfg["block_cd"] and word in ("cd", "pushd"):
                return "Blocked: `%s` is not allowed in Bash calls (%s)." % (word, cfg["cd_message"])
            if word in blocked:
                sub = ""                      # the first argument (a "+toolchain" selector is skipped)
                for x in toks[i + 1:]:
                    if x in OPS or set(x) <= set(";&|()\n"):
                        break
                    if not x.startswith("+"):
                        sub = x
                        break
                if sub not in blocked[word]["allow"]:
                    allowed = ", ".join(sorted(blocked[word]["allow"])) or "none"
                    reason = "Blocked: `%s` is not allowed here (allowed subcommands: %s)." % (
                        (word + " " + sub).strip(), allowed)
                    if blocked[word]["message"]:
                        reason += " " + blocked[word]["message"]
                    return reason
            at_start = False
        i += 1
    return None


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        path = (argv[1] if len(argv) >= 2 and argv[0] == "--config" else "") \
            or os.environ.get("BASH_GUARD_CONFIG") \
            or os.path.join(os.path.dirname(os.path.abspath(__file__)), "bash_guard.json")
        data = json.load(sys.stdin)
        cmd = (data.get("tool_input") or {}).get("command") or ""
        reason = check(cmd, load_config(path))
    except Exception:
        return 0
    if reason:
        print(reason, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
