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
- All of this has run under the tests (92 for the hooks, 54 for the plugin) and from the command
  line, not yet in a long live session.

## 2026-11-10: report lanes, restart commands, one orientation script, self-compact 0.6.1

Six fixes from one coordinator's notes after two compactions and a planning round, and one trial.

- New rules R-loop-15 to R-loop-18. A recurring background job is started by a script that
  prints its own restart command as its last line, and the carry lists that command
  (`tools/sweep_timer.sh`): after one compaction neither the summary nor the carry held the
  timer's command. Orientation after a compaction is one script that also checks the caps of
  the capped files (`tools/orient.sh`). An agent whose deliverable is a report writes its report
  file first (limit 200k, wrap-up from 170k): in one planning round 2 of 5 consultants reached
  the 150k limit before their report was finished. A late notice for a lane that is already
  closed is checked by its activity since the closing time, not by reading its output.
- R-route-8, a trial that is not yet measured: a reader builds a source index (path, line range,
  verbatim quote, why) before a consultant starts; the consultant opens the ranges itself and
  lists what the index missed. Stop if those lists are not short.
- R-loop-6: where the summary and the carry disagree, the carry wins.
- `hooks/context_guard.py` (plugin `playbook-hooks` 0.2.0): `"deliverable": "report"` in an
  `agent_types` entry gives that type report wording. Its notices ask it to write and finish
  its report file, not to update a HANDOFF block, commit or push. Types without the key get the
  same texts as before. `context_guard_ctl.py show` marks such types; the example config has a
  `consultant` type.
- `tools/agent_scan.py`: `--agent <id or prefix>` prints one sub-agent in detail (state, context,
  first and last activity, tool calls, its last text); `--since HH:MM|ISO|90m|2h` adds its
  activity since then and names the date and time it used; both work with `--json`. A transcript
  timestamp without an offset is read as UTC.
- `plugins/self-compact/` 0.6.1: when a queued message starts the turn after a compaction
  first, the resume prompt is held back. It is dropped if the main agent made 10 or more tool
  calls by the end of that turn, and sent then if it made fewer
  (`resumeSkipAfterToolCalls`, default 10; 0 = send at once, as in 0.6.0). The default is above
  what orienting after a compaction takes (7 or 8 calls with the starter kit's checklist),
  because a wrong drop leaves an unattended agent idle and a wrong send costs one turn. If
  that turn's end is never reported, the next main turn's end decides, or the plugin does
  when it finds no tool call of the main agent in a 10-minute window (so 10 to 20 minutes
  after the last one). A new request, `cancel_compaction` or the session ending drops a held prompt, and a
  dropped prompt no longer swallows a later prompt with the same text. The carry block says
  that it wins over the summary.
- `plugins/self-compact/` 0.6.1, fixed: a sub-agent's turn that started while a request waited
  was taken for a turn of the main agent. The compaction then waited until the main agent's
  next turn ended (seen in the plugin's log). Only the main agent's turns count now.
- STARTER-KIT.md: lane ledger and duties log files, a "Restart commands" line in the carry
  template, a source-index brief template (3.5), a consultant agent definition.
- Tests: 100 for the hooks, 31 for the tools, 74 for the plugin. None of this has yet run in a
  long live session; the order of queued messages after a compaction, and whether 1.5 s is
  enough for one to start its turn, cannot be tested outside one.
