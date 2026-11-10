# Starter kit: day 1 of a new multi-agent project

Step-by-step setup for running a project the way [PLAYBOOK.md](PLAYBOOK.md) describes. Paths below use
`ops/` for coordinator tooling, `notes/` for agent notes and `usage/` for cost and quality logs; put them in a git-ignored dir of the
project (e.g. `out/`). Replace `<...>` placeholders. Rule ids (R-...) point into PLAYBOOK.md.
A lane in an auto-cleaned worktree commits its notes on its branch instead (R-ho-7).

## 1. Day-1 order

1. Write the goal file: targets with numbers and a date, the goal metric, what counts as correct,
   what the user reserves for themselves (R-user-6). Keep it short. Choose the lane context limit
   for this project's workload (R-ctx-3: 80k for many short tasks, 150k by default, 160k for long,
   complex work) and write it into the rules block.
2. Create the agent definitions (section 6) and restart the session so they load (R-route-6).
3. Create the files in section 2 (hints, duties registry, findings, resume sheet, rules block,
   quality log, dropped-levers list, orchestration log).
4. Install the hooks from [hooks/](hooks/README.md), as copied scripts or once as the plugin
   `playbook-hooks` (then the project holds only the config files): context guard (notices at the
   wrap-up point and at the limit, a stop that holds for a lane that keeps going, foreground wait
   cap 270 s), Bash guard (no `cd`, no heavy local builds) and the reminder after a compaction.
   Verify with one blocked test command each. If the coordinator
   is to compact itself (R-loop-6), install the `self-compact` plugin
   ([plugins/self-compact/](plugins/self-compact/README.md)).
5. With several build hosts, set up the job queue before the first wave (with one machine, skip
   it and use `bgrun.sh` / `bgwait.sh` from section 7): one queue for all hosts, detached runs, `wait <id>
   --timeout`, `adopt`, `cancel`, priorities with aging, slots, a dirty-tree check at start.
6. Start the daemons (section 4) and verify each.
7. Write the brief template and the successor template (section 3); lint them.
8. Launch the first wave small (3-5 agents). Run the agent scan (`tools/agent_scan.py`) at
   ~5 min and look at agents above 70 % of their limit while still in setup: fix the brief or
   raise the limit (R-ctx-3) with `context_guard_ctl.py set --agent <id> --limit <N> --for 2h
   --reason "<why>"`.
9. Start the 30-min sweep timer. First sweep within 30 min of the first launch.

## 2. Files to create

### 2.1 Hints file (`ops/hints.md`)

```markdown
# Coordinator hints: facts and decisions I forgot before

PROTOCOL: After every compaction read ops/rules-digest.md fully, then the entries here newer
than its "covers through line N" header; grep this file for specifics, never read it whole.
First reply after a compaction says: "rules digest read (hints through line N), newest entry:
<...>". New standing user instruction -> a USER line here, a digest line AND a duties-registry
row in the same turn. Append-only (entries are cited by line number). Before changing a rule, grep its rationale here,
in the playbook and in the resume sheet.

ENTRY FORMAT (one physical line each, newest last in its section):
- FACT <MM-DD HH:MM>: <fact or rule> / WHY: <incident, numbers, job id> / CHECK: <duty name,
  hook, test, or "hint only: <reason>">
- USER <MM-DD HH:MM>: "<verbatim quote>" -> <what it means; scope; what it supersedes>
- MAIN <MM-DD HH:MM>: <coordinator decision> / WHY: ... / CHECK: ...
Append "SUPERSEDED <date>: <by what>" to a superseded entry's own line instead of deleting it.

## Waits and cost
## Launch prompts and briefs
## Infra and job queue
## Worktrees and disk
## Measurement
## Standing duties registry
## Stale tasks
## User decisions
```

### 2.1a Rules digest (`ops/rules-digest.md`)

```markdown
# Rules digest (covers through hints line N, <date>)
Every user decision and standing rule still in force, one line each, newest wins.
Regenerate every ~50 new hints lines or when a decision supersedes a line here.
## Security and permissions
- <rule> (H<line>)
## Compute and hosts
## Agents and briefs
- Lane context limit <N>k. Change it for running agents: `python3 .claude/hooks/context_guard_ctl.py set --agent <id> | --type <t> | --default --limit <N> --for 2h --reason "<why>"`; `show` lists what is in force. (H<line>)
## Releases and gates
## Current state (one line, date)
```
Budget: hard cap 15,000 bytes and 400 bytes per line, rules in force only. The hints file keeps the full
history; this file is what gets read.

### 2.2 Standing duties registry (section inside the hints file)

| instruction (user, date) | enforced by | cadence | evidence the checker reads |
|---|---|---|---|
| check and resolve bottlenecks | bottleneck scan in the sweep; `BOTTLENECKS:` part required | 30 min | last SWEEP line |
| clean stale agents and tasks | agent scan + `STALE:` line | 30 min (flag after 70) | resume sheet |
| long-running agents not stuck | agent scan SILENT/NOMSG/POLLING/HUNG; jobs > 2 h listed | 30 min | scan output |
| reflect: projection vs targets | SWEEP reflection part | 30 min (flag after 40) | resume sheet |
| token efficiency review | `TOKENS:` part + context-efficiency script | 30 min | ctx-eff JSONL fresh |
| token types and spend | token-mix script, `MIX:` part | 30 min | last SWEEP line |
| progress per token | efficiency analysis role shares in SWEEP | 30 min | last SWEEP line |
| quota pacing | pace daemon + watcher wakes | 15 min / on deviation | pace file fresh |
| agent quality | quality-log row per finished agent | per finish | rows vs finished agents |
| dashboard current | importers after every push / measurement | per event | dashboard data timestamps |
| findings resolved | row same turn; unowned > 2 h, past due, closed without evidence | per event / sweep | findings file |
| prevent recurrence | changelog/hints line per incident within 4 h | per incident | playbook mtime |
| playbook current | playbook edited with every practice change | 4 h | playbook mtime |
| resumability | hourly STATE line; HANDOFF notes; detached jobs | 1 h | resume sheet |
| cache-TTL waits | rules block contains "TTL IS 5 MIN", no timeout > 270 | per sweep | rules file grep |
| launch prompts complete | lint of first prompts for rules phrases, placeholders | per launch | transcripts |
| laptop disk / quiet | laptop guard log; free-space duty | 30 min | df, guard log |
| metric review | daily duty | 24 h | metric notes mtime |
| keep the user informed | hint only (no evidence source) | — | — |

Add a row the same turn a new standing instruction arrives. A row whose enforcement is "hint
only" must say why no check exists.

### 2.3 Findings register (`ops/findings.md`)

```markdown
# Findings register
Every finding (incident, follow-up, backlog item, analysis result) gets a row the turn it
arrives. Owner = agent name, "main" or "user". A row closes only with a commit sha or job id.
Deferring a row that bears on a target needs the user's OK. Rows have exactly 8 cells.
Status: open | assigned | deferred(<who>: <why>) | closed <sha|job id>.

| id | found | finding | source | owner | next | due | status |
|---|---|---|---|---|---|---|---|
| F1 | 10-12 10:10 | <one line> | <report/job/path> | main | <next step> | 10-13 18:00 | open |
```

Checks: unowned > 2 h, past due, closed without sha/job id. After each push to main, close the
rows whose id appears in the pushed messages: `git log <old>..<new> | grep -oE 'F[0-9]+([^0-9]|$)'`.

### 2.4 Resume sheet (`notes/resume.md`)

One physical line per entry, starting with the date from `date '+%m-%d %H:%M'`:

```text
STATE 10-12 14:05: live=<n> agents (<names>); queued jobs <ids>; next=<exact next step>; quota 7d <x> % pace <+/-y> pp
SWEEP 10-12 14:30: PROJ <goal metric now -> projected vs target>; BOTTLENECK <one>; STOP <work to stop>; IMPROVE <one method change>; TOKENS <ctx-eff flags, burn vs need, top consumer, tuning step + last step's effect>; MIX <read/write/output %, role shares>; BOTTLENECKS <WAIT/IDLE-HELD/UNDERUSED/FULL or none>
STALE 10-12 14:31: <what was cleaned: shells, jobs, slots, rows, worktrees> | none
CLEAN 10-12 14:40: <cleanup run, paths/archive log line>
AGENT <id> = <name> | branch <b> | worktree <path> | notes <path> | relaunch: <command>
```

### 2.5 Other logs

- `usage/quality.md`: `| date | agent | model/effort | task class | $ | calls | peak ctx | CI first pass | later fixes | gate delta | notes |`
- `notes/dropped-levers.md`: `| # | date | idea | owner/commit | target | expected | measured | why dropped | revive if |`
- `usage/orch.md` (hourly): `| time | quota 5h/7d | burn %/h | $ last 60 min | live agents | slots per host | alerts | action |`
- Ledger (`ops/trains.jsonl`), one JSON line per merge batch: `{"ts": local time, "batch": id, "commits": [...], "evidence": [job ids], "verdict": ..., "pushed": sha}`.
- Lane ledger (`ops/lanes.md`), one row per lane, written at launch and at close with times from `date`: `| lane | agent id | state | opened | closed | notes |`, state `live` or `closed`. `tools/orient.sh` prints the `live` rows; a late notice for a `closed` row is checked with `tools/agent_scan.py --agent <id> --since <closed>` (R-loop-18).
- Duties log (`ops/duties.log`): the duty checker's output, one line per run; `tools/orient.sh` prints its tail.

### 2.6 Rules block (`notes/agent-rules.md`, pasted or referenced as FIRST ACTION)

Keep each line one rule with its reason in brackets. Template (generalize the parts in `<>`):

```text
- Compute only via the job queue (<path>). Host routing: <builds/tests -> host A; scarce host B only for X>. Laptop heavy work only via <admission wrapper>.
- Never cd in Bash (absolute paths, git -C). Every clock time comes from `date`.
- Private names never in commits or reports. No push unless you are the merge steward. No task suggestions in the user's interface.
- Never compact your own context (the compaction state is shared with the coordinator session).
- Context: your brief names your limit (default 150k). Keep a ROLLING "## HANDOFF (<name>)" block at the top of your notes, updated at every milestone commit (<= 10k: state, commits, job ids, numbers, numbered next steps; details by path). From 20k before the limit finish the current step and start nothing new. At the limit, or at your CALL BUDGET, stop your waiters and end with "HANDOFF ready".
- CALL BUDGET: your brief names a number of calls to the first commit or submit. Tell main at ~80 % and hand off at the budget.
- Lean starts: successors read ONLY the HANDOFF section and the passages the next step needs; target <= 80k at first report.
- After a HANDOFF the successor owns all data, jobs and cleanup; the predecessor deletes nothing. Successors run `queue adopt <id> --by <me>` for every live job first.
- PROMPT CACHE TTL IS 5 MIN: waits <= 45 min -> foreground `queue wait <id> --timeout 240` (never > 270 s); longer -> update your HANDOFF, tell main and END YOUR TURN (a successor collects the result). Only `queue cancel` stops a job; never kill another agent's shell.
- Submit long jobs detached with your agent name; distinct --dir per concurrent job; check within minutes that each job really runs; job commands propagate the exit code.
- Never commit, amend, switch branch or edit tracked files while a job from that worktree is QUEUED (the queue syncs at start).
- Never message a finished agent; acknowledgements go to main.
- No single tool output > ~20 kB: redirect to a file, read tail/grep. No image reads unless one decides something you cannot measure; then one downscaled crop.
- You may launch <= 2 cheap reader sub-agents for summary-only reads (exact paths, one question, <= 300 words, file:line). Strong-model sub-agents need main's OK.
- Every number names its commit, scope and metric. Judge changes by the real metric; proxies only to explore.
- Tests you add: run them red then green before listing them as done.
- Before implementing a fix: `git log origin/main --grep <finding id>`.
- Cleanups only via allowlist scripts; rm only with literal absolute paths or "${VAR:?}".
- Never wrap or move a command so a permission, hook or isolation check cannot see it; on a refusal send main the exact text and wait.
- No approval popups: never run anything that makes the harness ask the user (git internals, harness config, shell rc, paths outside your worktree/tmp/job dirs, sandbox overrides). Skip and report.
- Final report: <= 300 words, starts with Flags, ends with Safety and "Follow-ups:" (one line each with evidence path or job id).
```

Without a job queue, replace the queue lines: `queue run --detach` becomes a background
`bgrun.sh <log> <cmd>`, `queue wait <id> --timeout 240` becomes `bgwait.sh <log> 240`, and "adopt"
means that the successor takes over the wait.

Lint: every launch prompt (or the first Read of the rules file) must contain "Compute only via"
and "TTL IS 5 MIN", and no "PLACEHOLDER", `$(`, or `<RULES>`.

## 3. Brief templates

### 3.1 New lane

```text
You are <name> (<agent type>), a NEW lane: <one-line goal>. The coordinator is "main". Never ask the user anything; questions and judgement calls go to main.
FIRST ACTION: Read <rules file> (binding; its LAST line = no approval popups).
SECOND ACTION: Read ONLY <file:lines>, <file:lines>. Do not read whole files.
WORKTREE: <absolute path> (branch <b>, tip <sha>). Literal absolute paths, git -C, no cd. <May commit | evidence lane: no tracked-file changes>. Notes: <path> (untracked; commit only while none of your jobs is queued).
DECISIONS (binding): (a) USER <date>: "<quote>" -> <meaning>. (b) MAIN: <decision>.
FACTS (do not re-derive): <fact> (<job id/commit>); <fact> (<path:line>).
ENTRY POINTS: <file:line> <what is there>; <file:line> ...
STEPS (each with a done-condition):
 1. <slowest external dependency first: submit probe/job X within 10 min> -> done when <...>
 2. <...>
EVIDENCE: <exact job command(s)>, host <h>, client env <VARS>, slots <n>, --dir <d>.
ACCEPT RULE (set before results): <criterion that closes the item>.
CONTEXT: limit <k> (wrap-up from 20k before it; keep the "## HANDOFF (<name>)" block rolling). CALL BUDGET: <n> calls to the first commit or submit.
REPORT to main (<= 250 words): Flags, what changed (commits), evidence (job ids, numbers with commit+scope), lane (FAST/TRAIN), Safety, Follow-ups.
```

### 3.2 Successor

```text
You are <name+1> (<type>), the in-lane SUCCESSOR of <name>. The coordinator is "main". Never ask the user; questions go to main.
FIRST ACTION: Read <rules file>. Then Read <predecessor's brief file> (binding where this brief does not override it).
SECOND ACTION: Read ONLY the "## HANDOFF (<name>)" section of <notes path>. Do not re-derive.
WORKTREE: same as predecessor: <path>, branch <b>, HEAD <sha>. <Commit allowed only after every adopted job has STARTED.>
ADOPT: `queue adopt <id> --by <name+1>` for <ids>; follow them with the wait rule; resubmit only on cancelled/infra-failure exits.
MAIN DECISIONS (binding): (a) ... (b) ...
NEXT STEPS (numbered jobs, so the handoff point is a job boundary): 1. ... 2. ...
FIRST CODE EDIT by call 5. Read nothing beyond the passages named here.
CONTEXT: limit <k>. CALL BUDGET: <n>. REPORT: as the predecessor's brief.
```

### 3.3 HANDOFF section (top of the agent's notes file)

```markdown
## HANDOFF (<name>) <date from `date`>
- STATE: <one paragraph: where the work is>
- COMMITS: <branch> <sha> <one line each>
- LIVE JOBS: <id> <host> <what> <done-condition> <output path>
- NUMBERS: <metric = value (commit, scope, job id)>
- DECIDED / DO NOT RE-DERIVE: <facts with evidence>
- FAILED APPROACHES: <what and why, one line each>
- NEXT (numbered jobs): 1. ... 2. ...
- CLEANUP OWED (successor deletes): <paths>
- FOLLOW-UPS: <finding line with evidence>
```

Budget: <= 10k. Details by path, not inline. Keep it rolling: update it at every milestone commit,
not only at the end.

When the coordinator launches the successor, it closes the predecessor in the same message:
"LANE CLOSED: no commits, submits or cancels; on a job end only report the id and result to main."

### 3.4 Coordinator compaction: carry block and resume prompt

```text
CARRY (<= 1.5 KB, written fresh; no rules, no results):
Next step: <exact action>
Waiting on: <lane name> -> <expected event>, ...
Queued/running jobs: <id> owner <name> <what>
Background shells: <task id and purpose>
Restart commands: <purpose>: <exact command, e.g. the RE-ARM line of tools/sweep_timer.sh>
Open user questions: <...>
Open first (max 3 files): <rules digest>, <...>

RESUME PROMPT:
Read <rules digest>, run tools/orient.sh, confirm the hints line number, then continue with: <next step>.
```

Where the summary and the carry disagree, the carry wins: you wrote the carry just before the
compaction. Live background shells are shown again after a compaction; finished ones are not, so
a job that has to be started again needs its command in the carry (R-loop-15).

From self-compact 0.6.1, when another message starts the next turn first, the plugin holds the
resume prompt back. It sends it after that turn only if the turn made fewer than 10 tool calls
(`resumeSkipAfterToolCalls`; 0 = always send). Keep that number above the calls your orientation
takes (section 5.1: 7 or 8), or a turn that only oriented drops the prompt. Act on the carry's next step
at once either way.

The compaction instructions themselves are a fixed text kept in a project file: this cycle only,
at most 700 words (what the user asked, what was answered or promised, decisions and results with
file paths, what was in progress); no rules, earlier history or file contents.

With the `self-compact` plugin these are the three fields of its tool: `instructions`, `carry`
and `resume`.

Never call cancel on a compaction you did not request yourself in this context.

### 3.5 Source index (trial, R-route-8)

The coordinator starts the reader before the consultant, never the consultant itself.

```text
You are <name> (<reader | worker>), a READER for one consultant question. You judge relevance, not the answer.
QUESTION (for context only): <the consultant's question>
SOURCES: <path>, <path>, ...
WRITE <index path>, one entry per relevant passage:
  <path>:<from>-<to> | "<verbatim quote>" | <one line: why it matters>
Then a COVERAGE list with every source above: <path>: read in full | searched (<terms>) | not opened.
Never summarize a passage in place of quoting it. Report to main: the index path and "Flags: ...".
```

The consultant's brief then adds: "Read <index path>. Open its ranges yourself; read the key
sources in full when the decision depends on completeness. End your report with 'Found outside
the index': each relevant passage the index missed, as path:lines." Measure per round: consultants
that finished under the standard limit with a complete report, and the length of those lists.

## 4. Daemons and timers

| daemon | start | verify |
|---|---|---|
| alert daemon (loop 2 min) | `nohup <monitor> run >> <out>/monitor/run.log 2>&1 &` (one instance, flock) | `<monitor> status` shows a fresh loop time; alerts file mtime < 5 min |
| wait-alert watcher | coordinator background task: `<monitor> wait-alert --ignore "$(cat ops/ignore.txt)"` | exits on a test alert; relaunch after each wake |
| sweep timer | background task: `tools/sweep_timer.sh 30 <sweep script>` (the sweep script runs duties + agent scan + ctx-eff + bottlenecks + token mix) | notification arrives every 30 min with OVERDUE list; its last line `RE-ARM: <command>` starts the next round |
| quota logger | every 10 min to quota JSONL | duty flags staleness > 30 min |
| pace daemon + watcher | every 15 min; wake at +3/-4 pp, 90/100 %, hourly | pace file fresh; disagree flag vs dashboard |
| dashboard | stdlib HTTP server re-reading state every 30 s | page loads; data timestamps current |
| backfill feeder (optional) | preemptible filler jobs (lowest priority, never ages) | capped while merge jobs queue |
| laptop guard | memory/swap/disk poller, kills only own runaway work processes | log line every interval |
| host cleaners | owner-aware pruning of build caches of finished agents | log; never touches dirs named by queued jobs |

Every recurring background job is started by a script kept in the repository, and the script
prints the command that starts it again as its last line (R-loop-15). Put that command into the
carry block before a compaction.

Rules: restart a daemon after any change it reads; after an infra deploy run `queue status` and a
dry run of every subcommand before restarting daemons; never restart a daemon while one of its jobs
runs unless you accept losing it; renice laptop daemons to lowest priority.

## 5. Checklists

### 5.1 After every compaction (coordinator)

1. Read the rules digest, then run `tools/orient.sh` (newer hints lines, resume sheet head, live lanes, duties log tail, caps); write the "rules digest read (hints through line N)" line in the first reply. Fix any `OVER` line.
2. Read the last SWEEP/STALE lines of the resume sheet if the orientation output did not show them.
3. `date` (never extrapolate times from notes).
4. Queue status: live and queued jobs vs the carry block.
5. Agent scan: live agents vs the carry block; anyone HUNG, idle, finished without a report?
6. Check that the watcher and the sweep timer are running; relaunch if not.
7. Run the duty checker; act on OVERDUE.
8. Only then handle agent messages.

### 5.2 After a session restart

1. Everything in 5.1.
2. Agent definitions reloaded: check a fresh reader's first prompt has the new text.
3. Hooks active: one blocked `cd` test, one blocked > 270 s wait test.
4. Daemons: status of each (section 4).
5. Running sub-agents from the old session are replaced by successors that start from their
   HANDOFF blocks (R-ctx-1).
6. Re-check the cache TTL of the new session (measure from two calls ~6 min apart).

### 5.3 Agent launch

1. Brief from template; facts checked to exist (R-brief-4); effort set for design lanes.
2. Lint: rules phrases present, no placeholders.
3. Bookkeeping `launch` (status row, context limits, agent id list).
4. For a successor: adopt jobs in the same step, and send the predecessor "LANE CLOSED" in the
   same message (R-ho-9).
5. Slot caps of live agents summed <= 1.5x pool.

### 5.4 Agent finish

1. Findings rows for every follow-up.
2. Quality row.
3. Successor if work remains; otherwise archive the worktree (after merge or decision) and delete
   its remote build dirs via the cleaner.
4. Dashboard row.

## 6. Agent type definitions (frontmatter examples)

```markdown
---
name: engineer
description: Strongest-model engineer for all code that ships: core logic, tests, tooling, merges, reviews, and anything where a subtle error would not show up in tests.
model: opus
effort: medium
---
First invoke the token-saver skill. Search before reading; read line ranges; keep tool output short.
Delegate big-log reads to `reader`. Waits: poll <= 240 s if < 45 min; longer: update the HANDOFF, tell main and end (a successor collects).
Keep a notes file with a rolling HANDOFF block; stop at the limit in your brief. Every report: Flags, Safety, Follow-ups.
```

```markdown
---
name: worker
description: Strongest model, medium effort, for bounded checkable tasks that do NOT write shipped code: research, data runs and tallies, docs, report drafts, log triage.
model: opus
effort: medium
---
(same working-style block; plus: no committed code; report starts with "Flags".)
```

```markdown
---
name: reader
description: Cheap read-only helper for ONE narrow job: summarize a log, extract numbers, check whether a run finished, watch a long run.
model: haiku
effort: medium
tools: Read, Grep, Glob, Bash
---
Read only. Stay under 100k tokens. Report exact numbers with file:line; start with "Flags".
```

```markdown
---
name: Explore
description: Read-only code search and orientation on the cheap model (overrides the built-in Explore, which inherits the session model).
model: haiku
effort: medium
tools: Read, Grep, Glob, Bash
---
Locate code, list signatures and call sites. Never judge correctness of subtle code.
```

A consultant, advisor or reviewer whose deliverable is a report (R-loop-17):

```markdown
---
name: consultant
description: Read-only consultant: answers one question for the coordinator with a written report. Writes no code, makes no commits.
model: opus
effort: high
tools: Read, Grep, Glob, Bash, Write, Edit
---
Right after orientation, create your report file (path in your brief) with an outline: question, answer so far, evidence, open points, sources read. Fill it as you go, so a stop at any point leaves a usable report.
Read line ranges, not whole files; if your brief gives a source index, open its ranges and list relevant passages it missed under "Found outside the index".
When the context guard says so, finish the report file and end. Final message: the report path and "Flags: ...".
```

Set `model` to the strongest model your account serves; `opus` above stands for it.
Mark its type as a report type in the context guard's config, so that its notices ask for the
report file and not for a HANDOFF block and a commit:

```json
"agent_types": {"consultant": {"limit": 200000, "wrapup_margin": 30000, "deliverable": "report"}}
```

Check the exact model ids your account serves: an alias may map to an older model
generation. Byte budget: ~10 kB for the engineer definition.

A lean file for fan-outs (R-ctx-11). The body is the brief; the launch prompt carries only the
work-package id:

```markdown
---
name: batch-worker
description: Lean worker for one work package of a fan-out. The task message names the package id.
model: opus
effort: medium
tools: Bash, Read, Edit
skills: [token-saver]
omitClaudeMd: true
maxTurns: 110
---
<the full brief: goal, inputs and their formats, steps with done-conditions, limits, report format>
```

Copy-ready versions of these files are in [examples/agents/](examples/agents/). They preload a
`token-saver` skill; a minimal version is in [examples/skills/token-saver/](examples/skills/token-saver/).
Copy it into `.claude/skills/`, or remove the `skills:` line and the "invoke the token-saver skill"
sentence. The frontmatter field names are those of Claude Code in November 2026; check them
against the documentation of your version.

## 7. Helper scripts

[examples/scripts/](examples/scripts/) holds two small helpers for projects without a job queue:

- `bgrun.sh <log> <cmd...>` runs a command with all output in a log file and appends
  `BGRUN DONE rc=<n>` when it ends. Start it through the harness's run-in-background.
- `bgwait.sh <log> [seconds]` waits up to `seconds` (default 240, never above 270) for that line
  and prints the log tail. An agent polls with it in the foreground and keeps its cache warm.

[hooks/](hooks/README.md) holds the context guard, the command that changes its limits while
agents run, the Bash guard and the reminder after a compaction (step 4 of section 1). With the
plugin installed, the command is `context-guard-ctl` instead of
`python3 .claude/hooks/context_guard_ctl.py`.

[tools/agent_scan.py](tools/agent_scan.py) prints one line per agent of the running session:
state, context against its limit, idle time, calls in the last hour, cache rewrites, and the
flags CTX, OVER, SILENT, HUNG, POLLING, NOMSG and REWRITES (R-loop-5). Run it from the project
folder at every sweep:

```bash
python3 ~/.claude/playbooks/multi-agent/tools/agent_scan.py --config .claude/hooks/context_guard.json
```

It reads timestamps, token counts and tool names from the transcripts, nothing else.

[tools/orient.sh](tools/orient.sh) is the one orientation command after a compaction (R-loop-16):
the hints lines newer than the digest header, the head of the resume sheet, the live rows of the
lane ledger, the tail of the duties log, and the rules digest against its cap (`OK` or `OVER`).
The file names are set at its top and can be overridden from the environment. For every other
file with a cap, add one `cap_line` call at the end of the script.

[tools/sweep_timer.sh](tools/sweep_timer.sh) `<minutes> <command> [args...]` waits, runs the
command and ends with `RE-ARM: <command line>` (R-loop-15). Start it in the background; the
completion notice then carries the command for the next round.
