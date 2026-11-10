# Hooks

Three small hooks that enforce playbook rules which agents otherwise forget, plus the control
command the coordinator uses to change context limits while agents run. Python 3.8+, standard
library only, no network.

| File | What it does | Playbook rule |
|---|---|---|
| `context_guard.py` | Tells each agent how full its context is (agents cannot see that themselves) and when to wrap up; denies foreground waits that outlive the prompt cache; reminds about slow calls and background jobs | R-ctx-3, R-wait-1, R-ho-1, R-loop-6 |
| `context_guard_ctl.py` | The coordinator's command to raise or lower a limit at run time: for all lanes, one agent type, one agent, or its own compaction point, with an optional expiry | R-ctx-3 |
| `bash_guard.py` | Rejects `cd`/`pushd` and the commands you list (for example heavy builds that belong on a build host) | R-safe-8 |
| `after-compaction.example.txt` | Text a `SessionStart` hook prints after every compaction, so the coordinator re-reads its rules digest before it acts | R-loop-6, R-book-3 |
| `settings.example.json` | The hook entries for `.claude/settings.json` | |
| `*.example.json` | Example configs | |
| `tests/` | `python3 -m unittest discover -s hooks/tests` (59 tests) | |

## Install

From your project folder, with `PB` set to your copy of this repository:

```bash
mkdir -p .claude/hooks
cp "$PB"/hooks/context_guard.py "$PB"/hooks/context_guard_ctl.py "$PB"/hooks/bash_guard.py .claude/hooks/
cp "$PB"/hooks/context_guard.example.json .claude/hooks/context_guard.json
cp "$PB"/hooks/bash_guard.example.json .claude/hooks/bash_guard.json
cp "$PB"/hooks/after-compaction.example.txt .claude/hooks/after-compaction.txt
```

Then:

1. Merge the `hooks` block of `settings.example.json` into `.claude/settings.json` (shared with
   the team) or `.claude/settings.local.json` (only you).
2. Edit the three files you copied without the `.example` part: the limits per agent type, the
   blocked commands (the two in the example are placeholders), and the reminder text (it names
   files such as `ops/rules-digest.md`; use your paths).
3. Start a new session. Hooks are read when a session starts.

Check that they are live:

- Ask the agent to run `cd /tmp` in Bash. It must come back with
  "Blocked: `cd` is not allowed in Bash calls".
- Ask a sub-agent to run `sleep 1` with the Bash timeout set to 400000. It must be denied with
  "Bash timeout above 270 s outlives the 5-minute prompt cache".

Without a session, the same two checks from a shell:

```bash
echo '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"cd /tmp"}}' | python3 .claude/hooks/bash_guard.py; echo "exit $?"
```

```bash
echo '{"hook_event_name":"PreToolUse","agent_id":"a1","tool_name":"Bash","tool_input":{"command":"sleep 1","timeout":400000}}' | python3 .claude/hooks/context_guard.py
```

The first prints the reason and `exit 2`; the second prints a JSON object with
`"permissionDecision": "deny"`.

## context_guard.py

One script, registered for two events.

**Before a Bash call (PreToolUse).** A foreground Bash call with a `timeout` above 270 s is
denied, with the reason and what to do instead (a shorter timeout, or a background job polled in
steps of at most 240 s). Why: the prompt cache lives 5 minutes. A call that waits longer makes
the agent's next call write its whole context to the cache again, at 1.25 times the input price
instead of 0.1 times for a read. Sub-agents only by default: the coordinator waits through
background tasks and is exempt unless `wait_cap_applies_to_coordinator` is true.

**After every tool call (PostToolUse).** The hook reads the token counts of the agent's last
model call from its transcript and adds a one-line `[context-guard]` notice to the tool result
when one of these is true:

| Who | When | The notice says |
|---|---|---|
| sub-agent | context within `wrapup_margin` of its limit | how much is left; finish the step in hand, start nothing new, bring the HANDOFF block up to date |
| sub-agent | context at or past its limit | stop now, update the HANDOFF block, commit what is tested, end with "HANDOFF ready" |
| sub-agent | its limit was changed by the coordinator | the old and the new limit; this replaces the brief |
| coordinator | context at `coordinator_compact_at` | compact at the next natural point, with a carry block |
| any | a tool call took longer than `slow_call_s` | the next call rewrites the whole context; keep foreground waits at 240 s |
| sub-agent | it started a background job | poll in the foreground in steps of at most 240 s; for waits beyond about 45 minutes, hand off |

Each context notice is given once per agent and comes back only after the context fell again
(a compaction) or the limit changed. The two reminders have cooldowns.

### Config: `context_guard.json`

Next to the script (or `--config PATH`, or the environment variable `CONTEXT_GUARD_CONFIG`). All
keys are optional.

| Key | Default | Meaning |
|---|---|---|
| `lane_limit` | 150000 | Context limit of a sub-agent. 0 = no context notices for sub-agents. |
| `wrapup_margin` | 20000 | The wrap-up notice comes this many tokens before the limit. |
| `agent_types` | `{}` | Limits per agent type: `{"batch-worker": {"limit": 80000, "wrapup_margin": 10000}}` |
| `skip_agent_types` | `[]` | Agent types the hook leaves alone. |
| `coordinator_compact_at` | 180000 | Compaction notice for the main session. 0 = off. |
| `overrides_file` | `context_guard.overrides.json` | The dynamic layer, see below. |
| `wait_cap_ms` | 270000 | Longest foreground Bash timeout. |
| `wait_cap_applies_to_coordinator` | false | Apply the wait cap to the main session too. |
| `slow_call_s`, `slow_call_cooldown_s` | 300, 3600 | Slow-call notice and how often at most. |
| `bg_reminder_cooldown_s` | 1800 | Background-job reminder: how often at most. |
| `rearm_margin` | 10000 | A notice comes back after the context fell this far below its point. |
| `notes_hint` | `your notes file` | How the notices name the place of the HANDOFF block. |
| `state_dir` | `~/.cache/context-guard` | One small state file per (session, agent). Files older than 7 days are removed when the guard next gives a notice. |
| `log` | off | A JSON-lines file with one line per notice and denial. |

The limits by workload (80k for many small tasks, 150k for a standard lane, 160k for long and
complex work) and the reasons are in the playbook, R-ctx-3.

### Changing limits while agents run: `context_guard_ctl.py`

The guard reads both of its files on every tool call. So a change takes effect at the next tool
call of every running agent, with no restart, and the guard tells an agent whose limit changed.

```bash
python3 .claude/hooks/context_guard_ctl.py show
```

```bash
python3 .claude/hooks/context_guard_ctl.py set --default --limit 160000 --for 3h --reason "wave 3: long refactors"
```

```bash
python3 .claude/hooks/context_guard_ctl.py set --agent <agent id> --limit 200000 --label lane-x --for 90m --reason "one more module to read"
```

```bash
python3 .claude/hooks/context_guard_ctl.py clear --agent <agent id>
```

- **Scopes**, most specific first: `--agent ID` (the id the Agent tool returned), `--type T`
  (an agent type), `--default` (all lanes), `--coordinator` (its own compaction point). The most
  specific entry wins, and any entry wins over `context_guard.json`.
- **Expiry**: `--for 45m|3h|1d` or `--until <ISO time with offset>`. Without either, the entry
  stays until `clear`. `clear --expired` removes the ones that ran out, `clear --all` everything.
- **More fields**: `--wrapup N` (wrap-up point, default limit minus the margin), `--note N` (an
  extra early notice that only asks for an updated HANDOFF block), `--label` and `--reason` (for
  the log).
- The command writes only `context_guard.overrides.json` (atomically) and appends every change
  to `context_guard.changes.jsonl` beside it: time, action, scope, and the old and the new entry
  with reason and expiry. That file is the record R-ctx-3 asks for. The base config is never
  edited.
- `tools/agent_scan.py --config .claude/hooks/context_guard.json` reads the same two files, so
  its CTX and OVER flags follow a changed limit.

The coordinator runs these commands itself. Put the three lines it needs into your rules digest
(see `STARTER-KIT.md`, section 2).

### Other flags

`--log PATH` and `--state-dir PATH` override the config. `--trace PATH` writes one line per call
(for measuring). `--tag TEXT` keeps a second registration of the hook apart from the first.
`--only-if-prompt-contains TEXT` gives the notices only to agents whose first prompt holds that
text (the wait cap applies to all sub-agents either way).

## bash_guard.py

Runs before every Bash call of the coordinator and of all sub-agents. It rejects a command
(exit code 2; the reason goes to the agent) when, at a command position (the start, or after
`&&`, `||`, `;`, `|`, `&`, `(` or a newline), it

- runs `cd` or `pushd`. The rule is absolute paths and `git -C <dir>`. A `cd` by the
  coordinator moved the start folder of a sub-agent launched later; that is how the rule came
  about.
- runs a command listed under `blocked_commands` with a subcommand that is not allowed there.
  Typical use: builds and test runs that belong on a build host or in a job queue.

Text inside quotes counts as one word, so `queue run "cargo test"` passes while `cargo test` does
not. Heredoc bodies are ignored. `block_cd: false` turns the first check off. Config:
`bash_guard.json` next to the script, `--config PATH`, or `BASH_GUARD_CONFIG`. Without a config
only `cd` and `pushd` are blocked.

## The reminder after a compaction

A compaction keeps state and drops rules: after one, a coordinator "simplified" a waiting rule
into the expensive form. `settings.example.json` therefore registers a `SessionStart` hook with
the matcher `compact` that prints `after-compaction.txt`. Claude Code adds a `SessionStart`
hook's output to the context, so the reminder is meant to follow every compaction, whoever
started it: the user, the automatic one, or the `self-compact` plugin in
`plugins/self-compact/`.

## How they behave when something is wrong

- **They fail open.** A missing or broken config, an unreadable transcript, a command the parser
  does not understand, a missing script: the tool call goes through, with no notice. The only
  blocks are the two described above. The command lines in `settings.example.json` are written
  so that a deleted script blocks nothing either.
- **Cost.** One short Python process per tool call, 20 to 50 ms on a laptop. The guard reads
  only the end of the transcript (256 KB, 2 MB at most).
- **What the guard reads.** Token counts and timestamps from the transcript of the agent that
  made the call. It never prints or stores transcript content.
- **Shell.** The command lines use a POSIX shell and `python3`. On Windows, run them through
  Git Bash or rewrite the three lines.
- **Tool names.** The matcher `Bash|.*_Bash` also catches a Bash tool that a harness exposes
  under a longer name.

## What has been used in the field and what has not

`context_guard.py` (context notices, wait cap, slow-call and background reminders) and
`bash_guard.py` are generalized versions of hooks that ran through the projects the playbook's
numbers come from (for example "9 long waits denied, soft-limit notices 37 -> 3" in R-wait-1).
The versions here were rewritten to remove project specifics.

New in this repository, and so far run only under the tests and from the command line, not in
a long session:

- the dynamic layer (`context_guard.overrides.json`, `context_guard_ctl.py`) and the "your limit
  changed" notice;
- the `SessionStart` reminder after a compaction;
- the generalized `blocked_commands` config of the Bash guard.

Please open an issue when something breaks: the hook contract (field names in the hook input) is the part most likely
to change between Claude Code releases. It was checked against the hooks reference at
https://code.claude.com/docs/en/hooks in November 2026.
