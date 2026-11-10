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

## 2026-11-10: a stop that holds, the hooks as a plugin, self-compact 0.6.0

- `hooks/context_guard.py`: past its limit a lane gets the stop notice again for every 10k of
  growth (`stop_repeat_every`); 20k past the limit (`hard_stop_margin`) it gets ten last tool
  calls (`hard_stop_grace_calls`) and then every tool call is refused until the coordinator
  raises its limit or its context falls. The coordinator gets one line per lane that passed its
  limit or reached the hard stop, with the command that raises the limit
  (`coordinator_lane_alerts`). Reason: of 580 lanes that got a notice, 22 ended more than 20k
  past their limit. Both are on by default; existing installs must change the context guard's
  PreToolUse matcher to `*` for the refusal to cover every tool (hooks/README.md).
- The guard finds `.claude/hooks/context_guard.json` by walking up from the project folder (a
  worktree below the project uses the project's file), does nothing with `--require-config`
  when there is none, and handles a tool call once when it is registered twice (it remembers
  the last 32 tool calls, so parallel calls are covered).
- `hooks/compaction_log.py`: a `PreCompact` hook that logs one line per compaction with its
  trigger (manual or automatic). No transcript content.
- The hooks are also a plugin, `playbook-hooks` (root `.claude-plugin/plugin.json`,
  `hooks/hooks.json`, `hooks/plugin-run.sh`, `bin/context-guard-ctl`): installed once, updated
  with one command, active only in projects that have a config file in `.claude/hooks/`. A hook
  that a project registers in its own settings is left to the project, so nothing runs twice
  while a project moves from copied scripts to the plugin.
- `plugins/self-compact/` 0.6.0, three options that are off by default: `remindAtTokens` (a
  note asks the main agent to request a compaction; it comes again in a long turn after every
  20k of growth), `autoCompactAtTokens` (the plugin compacts by itself at the end of a turn as a
  safety net, carrying only the fixed texts) and `instructionsFile` (the fixed summary
  instructions are read from a file at each compaction).
- All of this has run under the tests (91 for the hooks, 54 for the plugin) and from the command
  line, not yet in a long live session.
