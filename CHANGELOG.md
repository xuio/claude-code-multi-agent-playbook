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

## 2026-11-10: self-compact 0.5.0, the turn ends

- `plugins/self-compact/`: an agent often requested a compaction and then kept working, so the
  turn did not end and the automatic compaction fired first. Now the main agent's other tool
  calls are refused while a request waits (sub-agents are not touched), also in a turn that a
  prompt starts before the compaction ran; after six refused calls the plugin ends the turn
  itself. Options `blockToolsAfterRequest` and `allowAfterRequest`.
- R-loop-6 names this behaviour.

## 2026-11-10: default limits 150k / 130k / 180k, self-compact 0.5.1

- The default lane limit is 150k with the wrap-up point at 130k (it was 120k and 90k), and the
  coordinator's compaction point is 180k (it was 190k): R-ctx-3, R-loop-6, the context guard's
  built-in defaults, the agent scan's fallback values, the starter kit and the examples. This is
  the maintainer's chosen setting, not a new measurement; the measured numbers in R-ctx-3 are
  unchanged. A project sets its own values in `context_guard.json`, and the coordinator changes
  them at run time with `context_guard_ctl.py`.
- self-compact 0.5.1: `burstLimit` and `minContextTokens` default to 0 (off). They stay as
  options for sessions that fall into a compact-resume loop. The refusal of other tool calls
  after a request stays on by default.
