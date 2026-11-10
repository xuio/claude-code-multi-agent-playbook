# Hooks

Four small hooks that enforce playbook rules which agents otherwise forget, plus the control
command the coordinator uses to change context limits while agents run. Python 3.8+, standard
library only, no network. Copy them into a project ([Install](#install)) or install them once as
a plugin ([Install as a plugin](#install-as-a-plugin)).

| File | What it does | Playbook rule |
|---|---|---|
| `context_guard.py` | Tells each agent how full its context is (agents cannot see that themselves) and when to wrap up; refuses the tool calls of a lane far past its limit; tells the coordinator which lanes are over; denies foreground waits that outlive the prompt cache; reminds about slow calls and background jobs | R-ctx-3, R-wait-1, R-ho-1, R-loop-6 |
| `context_guard_ctl.py` | The coordinator's command to raise or lower a limit at run time: for all lanes, one agent type, one agent, or its own compaction point, with an optional expiry | R-ctx-3 |
| `compaction_log.py` | Logs one line per compaction (manual or automatic), so you can count how often the automatic one still fires | R-ctx-3 |
| `bash_guard.py` | Rejects `cd`/`pushd` and the commands you list (for example heavy builds that belong on a build host) | R-safe-8 |
| `after-compaction.example.txt` | Text a `SessionStart` hook prints after every compaction, so the coordinator re-reads its rules digest before it acts | R-loop-6, R-book-3 |
| `settings.example.json` | The hook entries for `.claude/settings.json` | |
| `*.example.json` | Example configs | |
| `hooks.json`, `plugin-run.sh`, `../bin/context-guard-ctl` | The same hooks packaged as the plugin `playbook-hooks` | |
| `tests/` | `python3 -m unittest discover -s hooks/tests` (92 tests) | |

## Install

From your project folder, with `PB` set to your copy of this repository:

```bash
mkdir -p .claude/hooks
cp "$PB"/hooks/context_guard.py "$PB"/hooks/context_guard_ctl.py "$PB"/hooks/bash_guard.py \
   "$PB"/hooks/compaction_log.py .claude/hooks/
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

## Install as a plugin

Copied scripts have to be copied again into every project after each fix. The repository is
therefore also a Claude Code plugin, `playbook-hooks`: the scripts are installed once, updated
with one command, and each project keeps only its own config files.

```
/plugin install playbook-hooks --marketplace xuio/claude-code-multi-agent-playbook
```

From a shell: `claude plugin install playbook-hooks --marketplace
xuio/claude-code-multi-agent-playbook`. On a build without `--marketplace`:
`/plugin marketplace add xuio/claude-code-multi-agent-playbook`, then
`/plugin install playbook-hooks@claude-code-multi-agent-playbook`. Then start a new session.

The plugin registers the hooks of `settings.example.json` for every session, and each of them
acts only in a project that has opted in with a file in its `.claude/hooks/` folder:

| File in `<project>/.claude/hooks/` | What it switches on |
|---|---|
| `context_guard.json` | the context guard (before and after every tool call) and the compaction log |
| `bash_guard.json` | the Bash guard |
| `after-compaction.txt` | the reminder after a compaction |

Copy them from the `*.example.*` files here and edit them; `{}` is a valid `context_guard.json`
(all defaults). The files are looked up walking up from the project folder, so a session in a
worktree below the project uses the project's files. In a project without them the hooks exit at
once and print nothing.

- **The control command** is on the Bash tool's `PATH` as `context-guard-ctl`, with the same
  arguments as `context_guard_ctl.py`: `context-guard-ctl show`, `context-guard-ctl set --agent
  <id> --limit 200000 --for 90m --reason "..."`. It finds the project's config from the current
  folder.
- **Update**: `claude plugin marketplace update claude-code-multi-agent-playbook`, then
  `claude plugin update playbook-hooks@claude-code-multi-agent-playbook`, then start new
  sessions.
- **Moving from copied scripts.** A hook that the project registers itself is left to the
  project: when a `"command"` line in the project's `.claude/settings.json` or
  `.claude/settings.local.json` names `context_guard.py`, `bash_guard.py`, `compaction_log.py` or
  `after-compaction.txt`, the plugin does nothing for that hook there. So installing the plugin
  changes nothing in a project that runs copied scripts, and nothing runs twice. Plugin hooks
  load when a session starts, so keep the project's entries until its sessions were restarted;
  then remove them, and the plugin takes over at the next tool call. The copied scripts can
  then be deleted; the config files stay.
- **A config in your home folder** (`~/.claude/hooks/context_guard.json`) is found from every
  project below it, because the lookup walks up. Put one there only if you want the guard in all
  of them.
- **For maintainers**: installed copies update only when `version` in
  `.claude-plugin/plugin.json` changes. Raise it with every change under `hooks/` or `bin/`.

## context_guard.py

One script, registered for two events.

**Before a tool call (PreToolUse).** Two checks. The hard stop for a lane that does not stop
is described in [When a lane does not stop](#when-a-lane-does-not-stop). The other: a foreground Bash call with a `timeout` above 270 s is
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
| sub-agent | still running, each `stop_repeat_every` tokens past the limit | the same, with how far past the limit it is and from which context its calls will be refused |
| sub-agent | context at its hard stop, and on each call after | how many tool calls it has left before every call is refused |
| coordinator | one of its lanes passed its limit, or reached its hard stop | the lane, its context and limit, and the command that raises the limit |
| sub-agent | its limit was changed by the coordinator | the old and the new limit; this replaces the brief |
| coordinator | context at `coordinator_compact_at` | compact at the next natural point, with a carry block |
| any | a tool call took longer than `slow_call_s` | the next call rewrites the whole context; keep foreground waits at 240 s |
| sub-agent | it started a background job | poll in the foreground in steps of at most 240 s; for waits beyond about 45 minutes, hand off |

Each context notice is given once per agent and comes back only after the context fell again
(a compaction) or the limit changed. The two reminders have cooldowns. When the same hook is
registered twice (for example in the project's settings and in your user settings), the second
run for a tool call does nothing: the state file records the `tool_use_id` of the last 32 calls
it handled, under a file lock, so parallel tool calls are covered too.

### Config: `context_guard.json`

Found in this order: `--config PATH`; the environment variable `CONTEXT_GUARD_CONFIG`; the first
`.claude/hooks/context_guard.json` found walking up from `$CLAUDE_PROJECT_DIR` to the filesystem
root (so a session in a worktree below the project finds the project's file); the file next to
the script. `context_guard_ctl.py` looks the same way and also walks up from the current folder.
With `--require-config` the guard does nothing at all when no file is found: a copy installed for
all projects stays silent in a project that has not opted in. Relative paths in the config
(`overrides_file`, `state_dir`, `log`) are relative to the config file's folder. All keys are
optional.

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
| `stop_repeat_every` | 10000 | Past its limit, the stop notice comes again each time the context grew this much. 0 = once only. |
| `hard_stop_margin` | 20000 | From limit + this, the lane gets its grace calls and then every tool call is refused. 0 = no hard stop. |
| `hard_stop_grace_calls` | 10 | Tool calls a lane still has after reaching the hard stop (notes, commit, push, stopping its jobs). |
| `coordinator_lane_alerts` | true | The coordinator gets one line per lane that passed its limit or reached its hard stop. |

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

### When a lane does not stop

Most lanes stop at the first stop notice. Some do not: of 580 lanes that got a notice, 86 reached
the stop notice and 22 of those ended more than 20k tokens past their limit (the worst by 153k
and 181k), because after that one notice nothing more happened. So:

- **Repeat.** Past the limit the stop notice comes again for every `stop_repeat_every` tokens of
  growth (default 10k), saying how far past the limit the lane is and from which context its
  tool calls will be refused.
- **Hard stop.** At limit + `hard_stop_margin` (default 170k for a 150k limit) the lane is told it
  has `hard_stop_grace_calls` more tool calls (default 10) for its notes, a commit, a push and
  stopping its jobs, and each later notice says how many remain. After that every tool call is
  refused in PreToolUse, with the reason: context N against limit L, end your turn now with your
  handoff report as your final message, the coordinator can lift this by raising your limit.
- **Lifting it.** The coordinator raises the lane's limit with `context_guard_ctl.py set --agent
  <id> --limit N --for 60m --reason "..."`. The block lifts at the lane's next call, because the
  overrides file is read on every call. It also lifts when the context fell (a compaction). If
  the raised limit still leaves the lane past the new hard stop, the lane is not locked out: it
  is told the new limit and gets a new set of grace calls.
- **Who is never refused.** The main session, agent types in `skip_agent_types`, and every lane
  when the lane limit is 0.
- **Switching it off.** `"hard_stop_margin": 0` turns off the refusal; `"stop_repeat_every": 0`
  brings back the single stop notice.

The PreToolUse check reads only the small state file that PostToolUse writes (the last known
context) plus the config and the overrides. Only when it is about to refuse a call does it read
the end of the transcript once more, to see whether the context fell since the last call. A
lane whose transcript cannot be read is never refused.

**The coordinator's line.** The coordinator learns of a lane over its limit without running a
scan: in its own PostToolUse the guard looks at the state files of the same session's lanes (at
most once every 30 s) and adds one line per lane when the lane passed its limit and one more when
it reached the hard stop; a lane that later passes a raised limit is reported again. Only lanes
updated in the last 15 minutes count. The line names the lane
(the description it was started with, else its agent type, plus the first 8 characters of its id)
and holds the exact command that raises its limit (paths quoted; from the plugin the command
is `context-guard-ctl`):

```
[context-guard] lane "Port the parser" (a1b2c3d4) is past its limit: context 160k, limit 150k. To raise it: python3 /…/.claude/hooks/context_guard_ctl.py --config /…/.claude/hooks/context_guard.json set --agent <id> --limit 190000 --for 60m --reason "..."
```

`"coordinator_lane_alerts": false` turns these lines off.

**Existing installs: change the matcher.** The refusal covers every tool, so the context guard's
PreToolUse entry needs the matcher `*`. If you installed it with `Bash|.*_Bash`, move the context
guard into its own PreToolUse entry with `"matcher": "*"`, as in `settings.example.json`; the Bash
guard keeps its Bash matcher. With the old matcher only Bash calls are refused.

### Other flags

`--log PATH` and `--state-dir PATH` override the config. `--trace PATH` writes one line per call
(for measuring). `--tag TEXT` keeps a second registration of the hook apart from the first.
`--only-if-prompt-contains TEXT` gives the notices only to agents whose first prompt holds that
text (the wait cap applies to all sub-agents either way). `--require-config`: see the config
section.

## compaction_log.py

A `PreCompact` hook. It appends one JSON line per compaction to
`~/.cache/context-guard/compactions.jsonl` (`--log PATH` to change it): the time, the session id,
the trigger as Claude Code gives it (`manual` or `auto`), the length of the custom instructions
(never their text) and the context size read from the end of the transcript. No transcript
content goes into the log. It prints nothing and always exits 0, so it never blocks or shapes a
compaction. Its purpose is to count how often the automatic compaction still fires:

```bash
python3 -c 'import json,collections,os; print(collections.Counter(json.loads(l)["trigger"] for l in open(os.path.expanduser("~/.cache/context-guard/compactions.jsonl"))))'
```

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
  blocks are the three described above (the wait cap, the hard stop, the Bash guard). The command lines in `settings.example.json` are written
  so that a deleted script blocks nothing either.
- **Cost.** One short Python process per hook and tool call, 20 to 50 ms on a laptop; the
  context guard now runs twice per call (before and after). After a call it reads only the end of
  the transcript (256 KB, 2 MB at most) and rewrites its small state file; before a call it reads
  the state file only. The coordinator's scan of its lanes runs at most every 30 s and opens only
  the files of its own session written in the last 15 minutes.
- **What the guard reads.** Token counts and timestamps from the transcript of the agent that
  made the call. It never prints or stores transcript content.
- **Shell.** The command lines use a POSIX shell and `python3`. On Windows, run them through
  Git Bash or rewrite the three lines.
- **Tool names.** The Bash guard's matcher `Bash|.*_Bash` also catches a Bash tool that a
  harness exposes under a longer name; the context guard's wait cap checks the same names itself
  under its `*` matcher.

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
- the generalized `blocked_commands` config of the Bash guard;
- the repeated stop notice, the hard stop with its grace calls and the refusal of every tool
  call, the coordinator's line per lane, the config lookup that walks up from the project folder
  with `--require-config`, the guard's no-op on a second run for the same tool call, and
  `compaction_log.py`. These have run only under the tests, not in a session yet;
- the plugin packaging (`hooks.json`, `plugin-run.sh`, `bin/context-guard-ctl`): validated with
  `claude plugin validate` and tested by running the registered command lines, not yet loaded
  by a live session.

Please open an issue when something breaks: the hook contract (field names in the hook input) is the part most likely
to change between Claude Code releases. It was checked against the hooks reference at
https://code.claude.com/docs/en/hooks in November 2026.
