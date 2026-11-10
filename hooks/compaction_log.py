#!/usr/bin/env python3
"""compaction_log -- Claude Code PreCompact hook: one JSON line per compaction.

Purpose: count how often the automatic compaction still fires, next to the ones a person or an
agent started. Each line holds the time, the session id, the trigger as given in the hook input
("manual" or "auto"), the length of the custom instructions (never their text) and the context
size read from the end of the transcript. No transcript content is written.

  compaction_log.py [--log PATH]      default log: ~/.cache/context-guard/compactions.jsonl

Prints nothing, always exits 0 (it must never block or shape a compaction). Python 3.8+,
standard library only.
"""
import json
import os
import sys
import time

DEFAULT_LOG = "~/.cache/context-guard/compactions.jsonl"
TAIL_BYTES = 256 * 1024


def context_size(path):
    """Prompt tokens of the last real assistant message in the transcript tail, or None."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import context_guard as cg
        ctx, _ = cg.scan(cg.read_tail(path, TAIL_BYTES), None)
        return ctx
    except Exception:
        return None


def record(inp, now=None):
    ci = inp.get("custom_instructions")
    tp = inp.get("transcript_path")
    return {"ts": round(time.time() if now is None else now, 3),
            "session_id": inp.get("session_id"),
            "trigger": inp.get("trigger"),
            "custom_instructions_len": len(ci) if isinstance(ci, str) else 0,
            "ctx": context_size(tp) if isinstance(tp, str) and os.path.isfile(tp) else None}


def main(argv=None):
    try:
        argv = sys.argv[1:] if argv is None else argv
        log = DEFAULT_LOG
        if len(argv) == 2 and argv[0] == "--log":
            log = argv[1]
        elif argv:
            return 0
        inp = json.load(sys.stdin)
        if not isinstance(inp, dict):
            return 0
        path = os.path.expanduser(log)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(record(inp)) + "\n")
    except BaseException:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
