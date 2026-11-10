# Changelog

## 2026-11-10: first public edition

- PLAYBOOK.md: 139 rules in twelve areas (routing, context, waiting, briefs, handoffs,
  coordinator loop, bookkeeping, monitoring, compute, evidence, user interaction, safety), a
  failure-mode catalogue and the governance of the playbook itself.
- One adjustable context limit per lane (R-ctx-3): 120k by default, set by workload, raised or
  lowered by the coordinator on stated signals.
- RULES.md: the generated one-line digest for agents.
- STARTER-KIT.md, example agent files, a minimal token-saver skill and the `bgrun.sh` /
  `bgwait.sh` helpers.

## 2026-11-10: wait rule in the starter kit

- STARTER-KIT.md, engineer example: a lane with a wait above 45 minutes updates its handoff and ends, and a
  successor collects the result. The example still said "background + end turn", against R-wait-1.

## 2026-11-10: hooks, agent scan and the self-compact plugin

- `hooks/`: the context guard (context notices per agent, the 270 s foreground wait cap,
  slow-call and background-job reminders), `context_guard_ctl.py` (the coordinator changes a
  limit for one agent, one agent type, all lanes or itself while agents run, with an expiry and
  a change record), the Bash guard (no `cd`, configurable blocked commands), a reminder after
  every compaction, a settings example and tests. The guards are generalized versions of the
  hooks behind the playbook's numbers; the run-time limit layer and the compaction reminder are
  new and have not yet run in a long session.
- `tools/agent_scan.py`: one line per agent of a session (context against its limit, idle time,
  polling, hung tool calls, cache rewrites), with the flags of R-loop-5.
- `plugins/self-compact/` (0.4.0) and a marketplace file, so the plugin installs from this
  repository. Changes against 0.3.2: a burst limit instead of the fixed 10-minute rule,
  `alwaysInstructions` / `alwaysCarry` now reach self-compactions, sub-agents are refused, one
  "still pending" note per turn, over-long fields are refused and not cut, a dropped request is
  reported, log lines carry the session id.
- PLAYBOOK.md, STARTER-KIT.md and README.md point to these files where a rule's check names a
  hook or a tool (R-ctx-3, R-wait-1, R-loop-5, R-loop-6, R-safe-6, R-safe-8, monitoring table).
