# Multi-agent playbook for Claude Code

How to run one coordinator agent plus 5-20 parallel sub-agents on a real coding project with
Claude Code, so that the work stays correct, the compute stays busy and the token quota buys
progress. Distilled from about five weeks of daily use on several projects: a numerical engine
on shared build hosts, two apps, an ML pipeline and a large knowledge base.
The project facts are left out; the lessons and the measurements stay.

Measured with Claude Code, on Opus-class models for judgment and code and Haiku-class models for
bounded reading, in October and November 2026. Prices, cache rules and harness behaviour change:
treat every number as a starting point and measure your own.

Companion file: [STARTER-KIT.md](STARTER-KIT.md) (day-1 setup, templates, checklists).

## 0. How to read this

Every rule has three parts:
- **Rule**: what to do.
- **Why**: the generalized incident or measurement behind it.
- **Check**: how the rule is enforced (a script, a hook, a lint, a sweep step). A rule with no
  check decays within a day under load. That was observed many times.

Evidence tags:
- `[M]` measured (numbers from transcripts, monitors or job logs; sample size noted where known).
- `[I]` incident (one or a few concrete cases; real, but not a rate).
- `[U]` user decision or preference (binding for that user; check it for a new user).
- `[P]` platform fact (how the harness, cache or tools behave).
- `[O]` opinion or reasoning, not measured. Treat as a hypothesis and give it a metric.

Words used throughout:
- **Coordinator** ("main"): the top-level session. It plans, launches, merges, monitors, talks to
  the user. It writes little code.
- **Lane**: one topic of work with its own branch and worktree. A lane may run through several
  agents in sequence (predecessor, successor).
- **Brief**: the launch prompt of a sub-agent.
- **Rules block**: the standard rule list every brief includes.
- **Sweep**: the coordinator's periodic check-and-clean pass (every 30 min).
- **Hints file**: the coordinator's append-only log of facts and user decisions. A short **rules
  digest** of it is what gets re-read after a compaction.
- **Handoff**: a lane ends and a fresh successor agent continues from its written HANDOFF block.
- **Job queue**: one shared scheduler for all compute jobs (builds, tests, benchmarks), written
  here as `queue run`, `queue wait`, `queue adopt`, `queue cancel`. A project on one machine needs
  none: read `queue run --detach` as a background `bgrun.sh` and `queue wait` as `bgwait.sh`
  (`examples/scripts/`); "adopt" then means that the successor takes over the wait.
- **Merge train, merge steward**: finished lanes are merged in batches (trains) by one agent (the
  steward), which runs the full checks once per batch (R-ev-8, R-ev-9).
- **Self-compaction**: the coordinator compacting its own context at a point it chooses, through
  the harness's compact command or a plugin that exposes it as a tool (one is in
  `plugins/self-compact/`).
- **Token-saver skill**: a short skill with the reading habits of R-ctx-8; a minimal version is
  in `examples/skills/token-saver/`.

## 1. Principles

1. **Context size is the cost.** Each call re-reads the whole context. Cache reads were 60-70 % of
   all spend `[M]`. Keep every agent small; hand off early; keep tool output out of contexts.
2. **Token efficiency means progress per token, not tokens per call.** A cheap agent working on
   the wrong thing is waste. The biggest measured loss was spend drifting off the goal metric (the
   share of spend on the main goal fell from 70 % to 26-32 %), not polling (7 % of calls) or wakes
   (4-8 %) `[M]`. Other losses: levers judged by a proxy the real metric did not confirm, landing
   overhead (one-change batches, per-agent full evidence) and coordinator bookkeeping (18-20M
   tokens per 6 h) `[M]`. Measure role shares and goal delta every sweep.
3. **Every rule needs an enforcement.** Rules stated only in prose were broken again within hours,
   especially after compactions `[I]` (many cases). Prefer a hook, a lint, a duty check or a tool
   default.
4. **Measure, then change one thing at a time.** Attribute effects. Record confounders. Keep the
   old value so the change can be reverted.
5. **Write things down where the next reader will look.** Notes files, a hints file, a findings
   register, a resume sheet. Memory inside a context dies at the next compaction or handoff.
6. **Prevent recurrence.** After every incident: one rule, test or monitor check so that it cannot
   recur, or is caught within minutes `[U]`.
7. **Fresh, lean agents beat long-lived ones.** New task = new agent with a short brief `[U][M]`.
8. **The user's attention is the scarcest resource.** Decide what you may decide; ask only what
   only the user can answer, with a recommendation; never block the work on a question.
9. **Spend the quota; do not hoard it.** Quota unused at the reset is lost `[U]`. Pace to use it,
   but efficiency beats smooth pacing.
10. **Correctness first.** A fast, cheap, wrong result is the most expensive outcome, because it
    corrupts baselines silently.

## 2. Roles, model routing and reasoning effort

### 2.1 Agent types

| type | model | effort | use for | never for |
|---|---|---|---|---|
| engineer | strongest model (Opus class) | medium; high for design/diagnosis | all code that ships (product, tests, tooling, infra), numerics, merges, reviews | — |
| worker | strongest model | medium | research, data runs and tallies, docs, report drafts, log triage, evidence collection | committed code (throwaway analysis scripts are fine) |
| reader | cheapest model (Haiku class) | medium (low risks missed flags) | one narrow read-only job: summarize a log, extract numbers, watch a run, map code | judgment, code, design |
| Explore override | cheapest model | medium | code orientation: files, signatures, call sites | exact edit spots in subtle code |

**R-route-1. Only two models: strongest for judgment and code, cheapest for bounded reading.**
- Why: a mid-tier model wrote a cache that passed its own tests and looked polished; a review by
  the strong model found 9 silent wrong-result bugs `[I]`. The mid tier saved only ~3-4 % of total
  spend, and its silent-failure code needed a strong-model review anyway `[M]`.
- Check: agent definitions name the model; a monitor reports spend per model.

**R-route-2. Keep cheap-model readers below their price-tier boundary (e.g. < 100k prompt).**
- Why: the cheap model is ~5x pricier above that tier `[P]`; narrow readers ran at 7-15k prompts
  with exact summaries `[M]`.
- Check: the reader definition says "stay under 100k"; spend monitor flags readers above it.

**R-route-3. Readers report, the strong agent judges. Every report starts with a "Flags" line.**
- Why: a helper tabulated "check mismatch=4" without flagging it `[I]`. A reader got totals right
  but categories wrong because its input was truncated `[I]`.
- Check: "Flags: ..." or "Flags: none" required by the definition; feed readers raw evidence,
  or name which fields are trustworthy.

**R-route-4. Delegate only when one helper replaces many calls or keeps big output out.**
- Why: delegating one status check replaces one strong call with one strong call plus a helper
  `[O]`. Readers saved ~2 % of spend when used for small things `[M]`. They pay off for big logs,
  diffs, other agents' notes and multi-file code maps.
- Check: agents may launch at most 2 cheap readers at a time without asking; each gets exact paths,
  one question, a <= 300-word answer with file:line refs. Strong-model sub-agents need the
  coordinator's OK `[U]`.

**R-route-5. Override the built-in Explore agent with a cheap-model project agent.**
- Why: the built-in one inherits the session's (expensive) model `[P]`.
- Check: a project agent file named `Explore` with the cheap model.

**R-route-6. Agent definition files load only at session start.**
- Why: edits did not reach a fresh test agent until a restart `[P]`.
- Check: until the restart, put new rules into every brief (the rules block).

**R-route-7. Keep agent definitions small; they are paid on every call.**
- Why: the engineer definition is re-read on every engineer call `[O]` (size ~10 kB).
- Check: a byte budget per definition (e.g. 10 kB); a new rule replaces one or justifies its
  bytes; rules that only some phases need go into briefs.

**R-route-8. TRIAL, not yet measured: a reader builds a source index before a consultant starts.**
1. The coordinator starts a reader first: the cheap model for logs and code maps, a strong worker
   for papers and design documents, where judging relevance is harder.
2. The reader writes a source index to a file. Per source: the path, the line range, a verbatim
   quote, and one line on why it matters. Plus a coverage list that marks every source it was
   given as read in full, searched, or not opened.
3. The consultant gets the question and the index. It opens the ranges itself and reads the few
   key sources in full when the decision depends on completeness. Under "Found outside the index"
   it lists each relevant passage the index missed.
- Why pointers and not summaries: a reader can miss a passage but must not be able to distort one.
  Readers got totals right and categories wrong, and reported a mismatch without a flag (R-route-3),
  so their summaries cannot carry a judgment `[I]`. Why the reader runs first and is not started by
  the consultant: an agent that waits more than 5 minutes for a child writes its whole context
  again (R-ctx-1), and the coordinator sees a reader it started itself `[P][O]`.
- Measure: how many consultants finish under the standard lane limit with a complete report, and
  how long the "Found outside the index" lists are. Stop if those lists are not short. Until then
  this is `[O]`.
- Check: the source-index brief template (STARTER-KIT section 3.5).

### 2.2 Reasoning effort

| role | effort |
|---|---|
| coordinator | high |
| engineer, design / diagnosis / hard merges | high (set per launch) |
| engineer, finishing, verification, measurement | medium (definition default) |
| worker, reader, Explore | medium |
| background watcher | low |
| one very hard decision | xhigh, never as default |

- Why: vendor data (one benchmark) puts medium ~2.5 points below high at ~70 % of the cost `[P]`.
  Own data: high produced ~45 % more output per call (921 vs 633 tokens) but cost only ~5-10 %
  more per call, because cache reads dominate `[M]`. No quality loss seen at medium for finishing
  work `[O]` (not measured rigorously).
- Check: the launch checklist sets effort for design lanes. It was forgotten once: design agents
  launched at the default medium `[I]`. Log effort per agent in the quality log.

## 3. Context economics

### 3.1 The cost model

- Prompt cache TTL is 5 minutes for sub-agents. The coordinator session may have a 1-hour TTL
  (subscription dependent; measure it) `[P][M]`.
- A cache read costs 0.1x input. A 5-min cache write costs 1.25x, a 1-h write 2x. Output costs 5x
  `[P]`.
- Each call re-reads the whole context. Cost per call is roughly 0.1 x context size `[M]`.
- Measured cost per call by context band (n = 20,548 calls): $0.06 (< 100k), $0.11 (100-200k),
  $0.18 (200-300k), $0.24 (300-400k), $0.45 (> 400k) `[M]`.
- Spend split: cache reads 60-70 %, cache writes 22-29 %, output 10-17 % `[M]`.
- A context grows ~35k base + ~1k per call `[M]`. A general-purpose sub-agent starts at ~63k
  tokens before its first action; a lean agent file starts at ~8-10k (R-ctx-11) `[M]`.
- What fills a big context: about a third the agent's retained reasoning, a third tool output
  (many medium outputs, not a few huge ones), plus code written and the base prompt `[M]`.

**R-ctx-1. Never wake or resume a big-context agent without need.**
- Why: 44 % of the spend in one 2.7-hour window came from 26 calls: resumes, handoff requests and
  old agents woken by job notifications ($3.5 each at 700k) `[M]`. A 6-agent rule broadcast cost
  ~$5 `[M]`. A message to an agent in a long wait cost a full rewrite at delivery ($1.1 at 222k)
  `[M]`.
- Check: a monitor alert on wakes (cache write > 150k tokens). Rules reach agents through the next
  brief, not messages. Message an agent only if its last call was < 4 min ago.
- An agent that has been idle for more than 5 minutes has lost its cache: do not resume it. Start
  a follow-up agent in the same worktree from its handoff note and the job log `[U]`. A message to
  an agent that has ended its turn resumes it, so decide between "more calls for the old agent"
  and "launch a follow-up", never both: one lane woke at 122k and edited the worktree in parallel
  with its own follow-up `[I]`.

**R-ctx-2. Fresh agent per task.** Resume an agent only for its own jobs, its own results,
questions about its own work, or the next step of its own deliverable. Agents past their wrap-up
point (R-ctx-3) get no new tasks.
- Why: the coordinator routed unrelated tasks to 190-210k agents; every call of theirs re-read
  200k while a fresh general-purpose agent starts at ~63k and a lean one near 10k `[U][I]`.
- Check: wakes of agents past their wrap-up point are flagged in the sweep.

**R-ctx-3. One context limit per lane: 150k by default, set by workload, adjustable by the
coordinator.** Size work in phases that end in a deliverable.
- Default: a lane hard-stops at 150k context. From 20k before its limit (130k) it finishes the
  current step and starts nothing new. Lanes overshoot a trigger by 30-40k `[M]`, so a lane can
  end past its limit; where that is too late, widen the margin or lower the limit. It keeps a
  ROLLING HANDOFF block at the top of its notes, updated at every milestone commit, so the stop
  loses nothing. One lane = one deliverable.
- Set the limit by workload, per project and per lane (in the brief):

  | workload | limit | basis |
  |---|---|---|
  | many short, similar tasks on a lean agent file (fan-out judging, page fixes, extraction) | 80k, plus `maxTurns` in the agent file | `[O]` estimate; here the limit is a guard against runaways, and tasks should end far below it |
  | standard lane with one deliverable (default) | 150k | `[U]` a chosen setting, not a measured optimum. Measured `[M]`: 7 lanes told to hand off at 200k all ran out at 132-150k; lanes with 100-110k triggers ended at 130-158k. Where your lanes run out before their limit, lower it |
  | long, complex work that must hold much context at once (design, cross-module change, multi-host build setup) | 160k | `[I]` 3 of 6 agents at 120-150k handed off during setup with no result |
  | report-only consultant on the strongest model (R-loop-17) | 200k, wrap-up from 170k | `[M]` 2 of 5 consultants hit 150k before their report was finished; an earlier one needed 200k |
  | coordinator | self-compacts from ~180k (R-loop-6) | `[U]` |
  | cheap-model reader | prompt < 100k (R-route-2) | `[P]` |

- The coordinator may raise or lower a limit: for one lane (in the brief), for a limited time (a
  wave or phase, with the end condition written down), or for the whole project (rules file).
  For agents that are already running it uses `hooks/context_guard_ctl.py` (one agent, one agent
  type or all lanes, with an expiry): the context guard reads the change at the agent's next tool
  call and tells the agent.
  Raise it when two or more lanes of a wave stop before their first deliverable, when a successor
  needs more than 20 orientation calls, when a defect traces to a handoff, or when a live lane is
  above 70 % of its limit while still in setup. Lower it when the project's tasks are small.
- Before raising, check the brief: one deliverable, at most 2-3 steps, the exact files and lines
  to read. Three lanes in a row hit their 120k limit through reading alone `[I]`; a higher limit
  would only have bought more reading. Raise when the brief is already tight and the task itself
  needs the context.
- What a raise costs: cost per call is ~0.1 x context, so 200k costs about a third more per call
  than 150k; a handoff costs ~5-20 orientation calls plus the note `[M]`. Past 200k the evidence
  is against it: phases past 400k cost $28-33 each, and two that overran ended in non-compiling
  or regressing work `[M]` (correlation, n = 3). Ask the user before going there.
- An agent that starts at ~63k (general-purpose) has ~50k less room under the same limit: give it
  a lean agent file (R-ctx-11) or count that in.
- Check: a context-guard hook (`hooks/context_guard.py`) posts notices at the wrap-up point and
  at the limit. One stop notice is not enough: of 580 lanes that got a notice, 86 reached the
  stop notice and 22 ended more than 20k past their limit `[M]`. So the hook repeats the stop
  notice every 10k, and 20k past the limit it gives the lane ten last tool calls and then refuses
  the rest until the coordinator raises the limit (new, not yet run in the field); the
  coordinator gets one line per lane that is over. Overrides per agent, per agent type and for
  all lanes live in one JSON file,
  written by `hooks/context_guard_ctl.py`; every change of a limit is recorded with reason, scope
  and end condition (the command appends it to a changes file); the sweep looks at lanes above 70 % of their limit ~5 min after a wave launch.

**R-ctx-4. Hand off only if real work remains (~50+ calls); finish short tails in place.**
- Why: a handoff costs ~5-20 orientation calls plus the note; successors oriented in 5-20 calls at
  about a tenth of the per-call cost `[M]`.
- Check: handoff template asks for "remaining steps as numbered jobs".

**R-ctx-5. Lean starts for successors.** A successor reads only the HANDOFF section and the
passages its next step needs. Target <= 80k context at its first report.
- Why: successors reached 155-245k and 17-38M tokens within 30 minutes because they read whole
  notes files and logs `[M]`. Lean successors sat at 67-75k `[M]`. A lower limit without lean
  starts made things worse: a 245k successor would hand off at once `[M]`.
- Check: a context-efficiency script flags LEAN > 80k at first report, RAMP > 10M tokens in the
  first 30 min, HO (handoff section) > 10k, HEAVY avg > 200k/call.

**R-ctx-6. Log to a file, look at a view.** Long, costly or noisy commands write full output to a
file; the agent reads `tail`, `grep -c`, `grep -m` or a summary line. If the view was too narrow,
read more from the file; never rerun to see more. No single tool output above ~20 kB.
- Why: outputs >= 2k tokens were ~4.5 % of all strong-model spend `[M]`. One 15 MB tool output
  pushed an agent to 187k and an early handoff `[I]`.
- Check: rules block line; the context-efficiency script shows ramps; the harness truncates very
  large outputs to a file.

**R-ctx-7. Probe-heavy and triage work: one call per probe series, a compact table per call.**
- Why: probing agents passed 400k in 45-60 min by reading raw probe output `[M]`. Triage lanes
  over a 9,000-record file ramped 10-17M tokens in 20 min `[M]`. A bounded question agent (46
  calls, $3.1) gave the best diagnosis of a day; an unbounded one (262 calls, 384k) cost $31 `[M]`.
- Check: briefs make "summarize to a <= 5 kB table (script or reader)" STEP 1; diagnosis briefs
  name ONE hypothesis with a stop condition.

**R-ctx-8. Token-saver habits in every agent.** Search before reading, read line ranges, tail and
grep logs, never dump whole files or JSON, no re-reads after edits, one bounded repair.
- Why: about -47 % weighted tokens per call and -60 % tool output after the switch (noisy, young
  contexts; sample: 160 calls after vs 3,980 before) `[M]`.
- Check: definitions and briefs say "invoke the token-saver skill first" (a minimal version of
  that skill is in `examples/skills/token-saver/`).

**R-ctx-9. Split "implement" and "verify" by design.**
- Why: big single-agent briefs ended at 180-213k before their verification `[I]` (3 cases).
- Check: brief template has separate implement and verify lanes for any non-trivial change.

**R-ctx-10. Batch independent reads and checks into one tool call.** Cost = calls x context, not
bytes read. Independent status checks, greps and file heads go into ONE shell call with separators;
dependent steps stay sequential.
- Why: lanes spent 75-107 calls (11-17M tokens) in their first 20 min on one-check-per-call
  starts `[M]`; for the coordinator each extra call at ~200k costs ~20k cache reads `[M]`.
- Check: the token-saver line in every brief; agent scan flags > 4 calls/min sustained.

**R-ctx-11. Fan-outs use a lean agent file.** For five or more agents sharing one brief, define a
project agent file with `omitClaudeMd: true`, a `tools:` allowlist (which also drops MCP tool
schemas), `skills:` to preload what the agent needs, and the brief as the file body. The launch
prompt then carries only the work-package id.
- Why: a general-purpose sub-agent started at ~63k tokens before its first action. About 48k of
  that was written fresh for every agent and re-read on each of its 50-87 turns, and it was mostly
  setup the agent never used: the project instructions file, memory, every tool schema, hundreds
  of deferred tool names and the skill list `[M]`. A lean file started at ~7.8k (n = 3), or ~9.9k
  with the brief as its body (n = 2): 84-88 % less first-turn context and cache writing `[M]`.
- Check: create the file before the session starts (R-route-6); measure the first turn of each
  new agent file once. The field names are those of Claude Code in November 2026: check them
  against the sub-agent documentation of your version, because an unknown field is ignored
  without an error.

**R-ctx-12. Turn off unrelated MCP servers and plugins per project.** Tool schemas and server
instructions of every connected server are paid in every context.
- Why: the coordinator's floor right after a compaction was 78-86k `[M]`.
- Check: project-scoped settings deny unrelated MCP servers and disable unrelated plugins. This
  takes effect in a new session only.

**R-ctx-13. Image reads have a budget.** A lane that looks at its own renders or screenshots
downscales them (<= 1400 px) and tiles several views into one image. The brief says: "no image
reads unless one decides something you cannot measure; then one downscaled crop".
- Why: two lanes spent most of 135-150k on full-size image reads and delivered nothing `[I]`.

**R-ctx-14. Cap concurrent bulk runs and estimate their tokens first.**
- Why: ~50 concurrent workflow runs of 8 agents each crashed the session; the runs were lost
  mid-way and the account hit its quota `[I]`.
- Check: at most 2 concurrent workflow runs unless the user says otherwise; queue the rest. A
  token estimate comes before any bulk job, and the user is asked above ~20M tokens `[U]`.

### 3.2 Waiting

**R-wait-1. Waits up to ~45 min: poll in the foreground, at most 240 s per call (never above 270 s). Longer waits: the
coordinator starts a background waiter and ends its turn; a lane writes its handoff and ends, and
a fresh successor collects the result.**
- Why: a cache miss rewrites the whole context at 1.25x; a warm poll reads it at 0.1x. At 200k one
  miss costs as much as ~12 polls `[M]`. A break-even estimate puts the switch at ~20 polls, i.e.
  ~80 min `[M]`; 45 min leaves margin. Two agents used the 600 s Bash maximum and paid two cold
  wakes (~$1.1-1.2 each) `[M]`. Cache rewrites were also seen after 244-269 s gaps `[M]`, so 240 s
  is the safe poll.
- Why lanes do not sleep through long waits: a wake after the cache expired rewrites the lane's
  whole context. Five parked lanes at 285-360k were woken once per job end at $1.0-1.7 each `[M]`.
  A successor on a lean agent file starts near 10k. The coordinator cannot be replaced, so it
  takes the one cache miss. Resume a parked lane only if its job ends within 5 minutes of the
  park `[U]`.
- Why the rule must be written with its reason: after a compaction the coordinator "simplified"
  it to a 590 s blocking wait, which is the expensive case `[I]`.
- Check: a PreToolUse hook (`hooks/context_guard.py`) denies sub-agent foreground Bash timeouts > 270 s `[M]` (after it: 9
  long waits denied, soft-limit notices 37 -> 3 in a few hours). A duty check fails if the rules
  block loses the "TTL is 5 min" line.

**R-wait-2. Wait on job ids, never on hand-typed log markers.**
- Why: a waiter grepped for `JOB_END` while the job wrote `job_DONE`; the agent sat idle 2 h `[I]`.
- Check: the job queue has a `wait <id> --timeout S` command that returns at job end.

**R-wait-3. Never run a long job as one foreground call.**
- Why: an agent hung 90 min in one call whose job had already failed `[I]`.
- Check: hook cap (R-wait-1); monitor stuck alert at 45/90 min.

**R-wait-4. The coordinator never blocks.** Anything over ~1 min runs in the background; the
coordinator keeps answering `[U]`.

**R-wait-5. Never spawn a sub-agent to wait on, or to classify the results of, jobs that have
not finished.**
- Why: a question agent used 82k tokens and 47 calls with no verdict; its job then failed in 4 s
  `[I]`.

**R-wait-6. Background work only through the harness.** Use the harness's run-in-background
mechanism; never start `nohup ... &` inside a foreground shell call. Foreground calls stay under
2 minutes.
- Why: a `nohup` timer started inside a foreground call coincided with every harness background
  shell dying: the watcher, the timers and all agents' waits. The daemons survived but could no
  longer notify anyone `[I]`.
- Check: after any mass shell death, check each daemon with `ps`, then relaunch watcher and timer.

**R-wait-7. Start a verification rerun in parallel with the original run**, not after it.
- Why: a result waited 36 min for its scoring and another 36 min for the serial rerun `[M]`.

## 4. Briefs

**R-brief-1. Structure every brief the same way** (template in STARTER-KIT): head, rules file,
named passages to read, worktree, binding decisions, facts with evidence, steps with
done-conditions, context limit and call budget, report format.

1. Head: name, agent type, lane, NEW or SUCCESSOR, who the coordinator is, "never ask the user;
   questions go to the coordinator".
2. FIRST ACTION: read the rules file (by path, see R-brief-3).
3. SECOND ACTION: read only named passages (HANDOFF section, specific lines).
4. WORKTREE: exact path, branch, tip; whether it may commit; no `cd`.
5. Binding decisions (user and coordinator), verbatim, with dates.
6. Facts with evidence (job ids, commits), so nothing is re-derived.
7. Task steps, each with a done-condition; evidence plan (which jobs, which hosts, env vars).
8. Context limit (R-ctx-3) and a numeric call budget (R-brief-9).
9. Report: length cap (<= 250-300 words), Flags, Safety, Follow-ups, lane for merging.
- Why: missing pieces each cost a round trip or a lost job `[I]` (many cases: missing env vars cost
  a full queue wait; wrong base branch; quoted commit that did not exist).
- Check: a launch-prompt lint scans each new agent's first prompt for required phrases and for
  placeholders.

**R-brief-2. Give file:line entry points and exact commands.**
- Why: a successor briefed with its predecessor's exact working job command reached its first
  report at 80k vs 115k `[M]` (n = 1). Inventory lanes given a list of systems to read ramped to
  201k in 30 min `[M]`.

**R-brief-3. One rules block, pasted or referenced by path; new rules go there first.**
- Why: handoff rules spread over ad-hoc prompts were missed `[I]`. A literal `$(RULES)` placeholder
  was sent instead of the text `[I]`; the Agent prompt is not shell-expanded `[P]`.
- Wave variant: for a wave of many agents, write the rules into one file and make "Read <file>"
  the FIRST ACTION. Verified 18/18 agents read it in call 1-2 (by parsing tool_use inputs) `[M]`.
  This saves ~17 kB per prompt.
- Check: the lint must see the full brief; a brief that only points at a file hides the rules from
  a lint that scans the first prompt `[I]`. If you use the file pointer, lint the transcripts for
  the Read call instead.

**R-brief-4. Check that brief facts exist before sending.** Commits, branches, finding ids,
"X is missing" claims.
- Why: a brief quoted a non-existent commit; another had the wrong base branch; a predecessor's
  "feature X is missing" was false `[I]`. A finding listed as open through three handoffs had been
  fixed on main hours earlier `[I]`; a lane spent ~1 h and 214k re-implementing a landed fix `[M]`.
- Check: briefs say "search the main branch log for <finding id> before implementing a fix".

**R-brief-5. Put the slowest external dependency first.**
- Why: probe lanes spent 160k on static analysis while their slow comparison runs sat queued, then
  cancelled them `[I]`.
- Check: successor brief template: "submit the probe in the first 10 minutes, read code while it
  queues".

**R-brief-6. Explicit caps instead of vague words.** "Short probes on the laptop" became 7,900
runs `[I]`. Give numbers: run caps, slot caps, memory caps, deadlines ("until job X ends", not a
fixed clock window that idles everyone else) `[I]`.

**R-brief-7. Set the accept rule before the probe runs.**
- Why: with the accept criterion written first, the probe result closed the item within minutes,
  no round trip `[I]`.

**R-brief-8. Studies end with a closer.** When a study's report lists unverified items, launch one
lean read-only closer at once.
- Why: a closer settled all open items in 6 min / 124k tokens, one of them a real defect site `[M]`.

**R-brief-9. A numeric call budget in every brief.**
- Why: a "context discipline" line alone did not stop ramps (171k and 119 calls in 30 min); a
  numeric budget to the first commit or submit did (18 calls, 65k) `[M]`. Budgets of 90-150 calls
  all overshot their context trigger and delivered about half the brief `[M]`.
- Calibrated values: doc-only 15; evidence re-check or reader successor 20-30; code plus test
  item 35-40 (+15 per fix round after a failed suite); feature implementation plus commit 45-60.
- The lane tells the coordinator at ~80 % and hands off at the budget instead of getting a raise.
  A budget caps exploration; it is not a stop signal for a nearly-done step.
- Check: every brief names the number; the agent scan compares calls to first commit with it.

**R-brief-10. One side or layer per lane; a lane that ports your own code to another platform only coordinates.** Each file port goes to
ONE sub-agent with a short brief (source file and range, target file, APIs, "write only this
file, build, 5-line summary"); the lane reads only summaries and build or test tails.
- Why: port lanes hit 130k after 1-2 items, mostly from reading sources; a lane that delegated
  one file per sub-agent finished 7 items `[M]`. Splitting a running two-sided brief into two
  parallel successors with a shared decisions file is the fix mid-flight.

**R-brief-11. Brief checklist.** Before sending: filter the items by their current status, search
for an existing fix, state every input's format, state key derivations exactly, and use a new
brief name.

1. Filter the item list by each findings row's status, not by an older triage list (6 of 52
   items in one brief were already closed `[M]`).
2. Search the findings register and the live briefs before filing a row or launching a fix lane
   (two lanes fixed the same bug for ~6 h `[I]`).
3. Open every input file and state its format in an INPUTS line; scripts list their local
   imports (three such gaps cost ~40 calls in one hour `[M]`).
4. State key and id derivations exactly (a hash-with-extension mismatch lost a full host round
   `[I]`).
5. The brief name is new, and the brief's first action reads the shared rules file.

**R-brief-12. Never ask a sub-agent for a full read of the coordinator's decision log.** Give it
the rules digest and let it grep for specifics.
- Why: "read the hints file completely" (~400 KB) took a lane to ~230k before it wrote any code
  `[I]`.

**R-brief-13. Choose the launch form by worktree situation.** A fresh code lane gets an isolated
worktree and commits at once; an in-lane successor and a lane in a second repository run without
isolation.

- Fresh code lane: an isolated worktree. Step 0 creates the lane branch from a named commit and
  makes a stub commit at once; the lane does not end a turn before its first commit. An unchanged
  isolated worktree is removed when the turn ends, and the agent wakes in the main checkout `[I]`.
  An isolated tree can also start at the last pushed commit instead of local HEAD: check the
  merge base and that the files the brief names exist `[I]`.
- In-lane successor in the predecessor's worktree: no isolation `[I]` (the isolation check
  refuses `git -C` into another worktree).
- Lane in a second repository: no isolation; it creates its own worktree of that repository as
  step 0 `[I]`.
- The coordinator never creates a worktree for, or submits a command of, an agent that a
  permission check refused (R-safe-2).

## 5. Handoffs

**R-ho-1. Handoff procedure (agent side).** At the trigger, FIRST write the HANDOFF section, then
continue nothing else:
1. Write `## HANDOFF (<name>)` at the top of the notes file (<= 10k): state, commits, live job
   ids, numbers, numbered remaining steps, dirs to delete, open findings. Details by path. Keep
   this block ROLLING: update it at every milestone commit, so a hard stop loses nothing
   (R-ctx-3).
2. Commit WIP and notes, but only if none of your queued jobs is still waiting to start (see
   R-job-4).
3. Stop your own background waiters (jobs keep running).
4. End with "HANDOFF ready". Do not call the self-compaction tool (R-safe-6).
- Why: successors overran their trigger (164-212k vs 160k) because each kept one more job wait
  after it `[M]`. Handed-off agents with live waiters were woken 5 times at $1.0-1.7 `[M]`.
- Check: the context guard posts the trigger; the context-efficiency script flags HO > 10k.

**R-ho-2. Successor side.** Same worktree, same branch. First actions: read the rules file, read
the HANDOFF section only, adopt every live job (`queue adopt <id> --by <me>`), follow them with the
normal wait rule, resubmit only on "cancelled" or "infra failure" exits.
- Why: launching a successor without adopting the predecessor's job gave orphan alerts and a lost
  job `[I]`.

**R-ho-3. After a handoff the successor owns all data, jobs and cleanup.** The predecessor never
deletes anything.
- Why: a woken predecessor ran its cleanup and deleted reference data the successor was using; a
  regeneration on the scarce host followed `[I]`.

**R-ho-4. Stop the predecessor only after its jobs are adopted and detached.** Never kill another
agent's job client shell.
- Why: stopping an agent cancelled its queued jobs (owner pid gone) `[I]`; stopping a predecessor's
  client killed an adopted job after 55-75 min `[I]` (2 cases). Fix that worked: detached job
  runners (double-forked, signal-immune when adopted) plus a separate `wait` client `[M]` (repro
  failed before, passed after).

**R-ho-5. Never message a finished agent**, not even to acknowledge. Acknowledgements go to the
coordinator `[M]` (~$1-2 per wake at 250k).

**R-ho-6. Every handoff gets a successor at once** unless the work is blocked or finished.
- Why: 7 handoffs without successors over a night cut burn to ~0.5 %/h and idled a rented host for
  > 1 h `[M]`.

**R-ho-7. Notes survive the worktree.** Agents in auto-cleaned (isolated) worktrees commit their
notes and helper scripts on their branch before ending.
- Why: auto-cleaned worktrees with no commits were deleted with their notes and scripts `[I]`
  (3 cases); one was rebuilt from the transcript.
- Check: rules block line; a recovery script can replay Write/Edit calls from a transcript.

**R-ho-8. Idle-between-tasks agents (stewards, waiters) use a durable worktree**, not an
auto-cleaned one `[I]`.

**R-ho-9. Close the predecessor in the message that launches the successor:** "LANE CLOSED: no
commits, submits or cancels; on a job end only report the id and result to the coordinator".
Successor briefs name every job the predecessor still owns.
- Why: a closed predecessor still wakes on its own job ends. One cancelled a job and committed
  in its successor's worktree; another committed notes there while the successor was running
  `[I]`.

**R-ho-10. Successor briefs are no-orientation.** Point at the HANDOFF block, name the exact
file:line spots, forbid reading beyond them, ask for the first code edit by call 5, decide open
questions before spawning, and give the test commands verbatim. Keep the brief under ~60 lines
with the binding rules inlined.
- Why: 6 of 8 call-budgeted lanes handed off; one successor spent 48 calls re-reading and landed
  one commit. Successors with such a brief finished in ~15 calls `[M]`.

**R-ho-11. Check that an inherited job is alive before waiting on it** (process list plus a log
modified in the last few minutes). If it is dead, requeue it under a fresh log name.
- Why: a successor waited ~55 min on a run that had died when its predecessor's lane ended `[I]`.

**R-ho-12. While a lane has queued jobs, its handoff note goes to an untracked file.**
- Why: a note written into a tracked file dirties the tree exactly like a code edit; three
  queued jobs synced the dirty tree and were void `[I]` (R-job-4).

**R-ho-13. A long-wait evidence lane is two phases.** Phase A submits and records job ids. Phase B
is a fresh lean successor that only waits and judges.
- Why: one lane spent 58 of its 60 calls on setup `[M]`. When a lane's head is known to be
  replaced, cancel its queued evidence jobs on the old head at once; waiting for doomed jobs to
  start cost 2-3 h per lane `[M]`.

## 6. Coordinator loop

### 6.1 Events

On every event (agent report, alert, job end, user message) the coordinator does ONE batched call
where possible: bookkeeping script + messages + launches as parallel tool calls `[M]` (coordinator
reads were 61 % of its cost; it is ~13-20 % of total spend).

| event | actions |
|---|---|
| agent launched | bookkeeping `launch` (status row, context limits, ids); add to the agent id list |
| agent reports | findings rows for every follow-up (same turn); quality row; decide merge lane; successor? |
| handoff ready | launch successor (adopt jobs in the same step); then stop the predecessor |
| alert | act or mute with a reason and expiry; prevention step |
| job end (headline/merge job) | check summary exists; update dashboard |
| push to main | close findings rows whose ids appear in the pushed commit messages; dashboard |
| user message | record decisions verbatim in the hints file (same turn) |

### 6.2 Sweep (every 30 min, timer-driven)

A background timer wakes the coordinator. Each sweep runs the duty checker, the agent scan, the
context-efficiency script, the bottleneck scan and the token-mix script, then writes one SWEEP line
with required parts:

- `TOKENS:` context-efficiency trend and flags, quota burn vs needed rate, top consumer, ONE tuning
  step and the effect of the last one `[U]`.
- `MIX:` token types and spend split by role `[U]`.
- `BOTTLENECKS:` queue waits, idle-held slots, under-used and full hosts `[U]`.
- Reflection: projected goal numbers vs targets, the single biggest bottleneck, work to stop, one
  method improvement `[U]`.
- A `STALE:` line: what was cleaned (or "none").

**R-loop-1. Stale sweep cleans, not only checks.** Hung, idle and spinning agents; obsolete
timers and watchers; queue jobs of finished owners; idle held slots; dashboard drift; finished
worktrees.
- Why: the user had to ask twice; the sweep had only been checking `[U][I]`.
- Check: the duty checker flags a missing STALE line after 70 min.

**R-loop-2. Duties live in a registry with an evidence source each, checked by a script.**
- Why: 6 duties were found overdue at once (a sweep log 11 h stale, a goal metric not measured for
  7 h because a daemon's "daily" job had never run, ~34 agents without a quality row) `[M]`. The
  duty checker had checked only that a SWEEP line existed, not what it covered `[I]`.
- Check: the duty checker reports OVERDUE per duty; the sweep notification lists them. Trust a
  daemon only from evidence of its output, never from its config.

**R-loop-3. Reflection each sweep: projected vs target, bottleneck, stop list, one improvement.**
- Why: reacting to alerts one by one hid that the overall trajectory was off `[U]`.

**R-loop-4. Bottleneck scan each sweep, and name the binding limit.** Slots held vs cores used,
declared vs written disk, RAM-scratch accounting, queue priority inversion.
- Why: a host showed 74/80 slots booked but ~34/96 cores used `[M]`; another sat at 120/120 slots
  held with load ~40 `[M]`; backfill jobs took slots while merge jobs waited (priority inversion)
  `[I]`; a host idled at 13/15 free while jobs waited 50-65 min elsewhere on a pinned disk limit
  `[M]` (re-pinning ran them at once).
- Check: bottleneck script flags WAIT, IDLE-HELD, UNDERUSED, FULL; every stopgap gets a hints line
  with its watch condition and an infra fix lane.

**R-loop-5. Agent scan each sweep.** Flags: SILENT (no transcript write > 15 min), NOMSG (> 60 min
without a report), POLLING (> 70 % of calls are polls), CTX (> 90 % of soft), HUNG (tool call
without result > 20 min).
- Why: a sub-agent hung inside a tool call for 80 min with status "running" `[I]` (2 cases; once
  in a git commit). A sub-agent self-compacted and then stopped, its jobs unattended ~25 min `[I]`.
- Check: HUNG -> move the work to a successor from the notes at once; wrap `git commit` in a
  120 s timeout in briefs.
- Tool: `tools/agent_scan.py` prints one line per agent with these flags (CTX there means at or
  past the wrap-up point), plus OVER (context past the limit) and REWRITES (three or more cache
  misses). With `--config` it uses the context guard's limits, run-time changes included.

**R-loop-6. Self-compaction of the coordinator.** Compact at natural points (after a merge push,
after a lane report is fully processed) from ~180k context `[U][M]` (it had been 230-300k; the
coordinator was 15-20 % of spend at avg 156k/call). Never compact with an unprocessed agent
notification or in the middle of a discussion with the user. The coordinator does not hand off
to a successor coordinator; it continues through compactions `[U]`.
- The compaction instructions are a FIXED text kept in a project file: this cycle only, at most
  700 words (what the user asked, what was answered or promised, decisions and results with file
  paths, what was in progress). No rules, earlier history or file contents; those live in files.
- The carry block (<= 1.5 KB, written fresh) holds the exact next step, the lanes the coordinator
  waits on (name -> expected event), background shells by purpose and, for each job that has to be
  started again, its exact command (R-loop-15), open user asks and at most 3 files to open first.
  No rules and no results.
- Where the summary and the carry disagree, the carry wins: the coordinator wrote the carry itself
  just before the compaction.
- After every compaction read the RULES DIGEST (R-book-3) fully, then run the orientation script
  (R-loop-16); it prints the hints entries newer than the digest's `covers through line N` header.
  Grep the hints file for specifics, never read it whole.
- Why: a full read of a 420 KB / ~1,060-line hints file put the coordinator at ~266k right after a
  compaction, past its own compaction target, and every later call paid for it again `[M][U]`.
- In the first reply after a compaction, write: "rules digest read (hints through line N), newest
  entry: <x>". The user cannot see tool calls; a silent read looks like no read `[U]`.
- If a full read is ever needed, split by bytes (`split -b 80000`) and read each piece; fixed page
  boundaries go stale as the file grows `[I]`.
- Check: the duties registry has a row for it; hints protocol at the top of the hints file.
- Tools: the `self-compact` plugin (`plugins/self-compact/`) gives the coordinator the compaction
  as a tool call with `instructions`, `carry` and `resume`. The compaction runs only when the turn
  ends, and coordinators often kept working after the request until the automatic compaction
  fired `[I]`; from 0.5.0 the plugin refuses the coordinator's other tool calls until the
  compaction has run. From 0.6.0 it can also remind a coordinator that does not ask
  (`remindAtTokens`), compact by itself at the end of a turn as a safety net
  (`autoCompactAtTokens`, nothing but the fixed texts is carried), and read the fixed
  instructions from the project file (`instructionsFile`). From 0.6.1, when another message
  starts the next turn before the resume prompt, the plugin holds the resume prompt back. It
  sends it after that turn only if the turn made fewer than 10 tool calls (option
  `resumeSkipAfterToolCalls`; 0 = always send; keep it above the calls your orientation after
  a compaction takes, 7 or 8 with the starter kit's checklist). The coordinator still acts on the carry's next step at once. A `SessionStart` hook with the
  matcher `compact` (`hooks/after-compaction.example.txt`) prints the reminder to read the
  digest, and `hooks/compaction_log.py` logs every compaction with its trigger, which shows how
  often the automatic one still fires.

**R-loop-7. Never type a clock time by hand.** Every time comes from `date` in the same command
that writes it.
- Why: after several compactions, notes timestamps ran ~1 h ahead of the real clock `[I]` (twice).

**R-loop-8. Record every cleanup in the resume sheet in the same step.**
- Why: "disappearing worktrees" were the coordinator's own archive run, forgotten across a
  compaction `[I]`. The archive script now logs every removal.

**R-loop-9. Lost-decision audit after long multi-compaction sessions.** Extract the deduplicated
user messages with a script, read them once, diff against memory and the hints file `[I]`
(~480 messages / 70k chars found several unrecorded decisions).

**R-loop-10. Re-aim agents the moment a lever lands in shared code.** Message every agent working
on the same code, naming the owner and the commit.
- Why: two agents committed the same fix within an hour `[I]`.

**R-loop-11. Admission control before a wave.** Sum the slot caps of live agents; keep it <= ~1.5x
the pool.
- Why: 12 agents requested ~70 slots against a pool of 15 `[M]`.

**R-loop-12. Bookkeeping rides along.** Log lines, hints appends, digest edits and plan updates go
into the same message as real work, never a call of their own. The periodic sweep prints one
count line plus the non-OK lines; the full output stays in its log.
- Why: each extra coordinator call at ~200k costs ~20k cache reads `[M]`.

**R-loop-13. An advisor model signs off strategy, not operations.** If a stronger consultant
model reviews the coordinator, it signs off strategic items only (new decisions, rules, strategy,
results), in batched check-ins triggered by content: due once a result or decision has waited
~30 min unsigned. Operations stay with the coordinator.
- Why: a check-in every 30 minutes produced about a dozen advisor calls in one night, mostly
  about one cheap run; the advisor itself flagged it `[I][U]`.

**R-loop-14. A scheduled follow-up must be proven runnable.** After creating a scheduled task,
run a throwaway probe once and check which model and configuration it started with.
- Why: a morning check died in 1.5 s on a model id that was no longer available `[I]`.

**R-loop-15. A job that must be started again carries its own command.** A recurring background
job (sweep timer, watcher) is started by a script kept in the repository. The script prints the
command that starts it again as its last output line. The carry block lists the background shells
by purpose and, for each job that has to be started again, the exact command.
- Why: after one compaction, the completion notice of a 30-minute sweep timer was the first thing
  to arrive. Neither the summary nor the carry held its command; the carry rule then said
  "background shells by purpose only". The coordinator recovered the command from the session
  transcript with 4 tool calls, one of them a grep with 1,148 matches `[I]`. Live background shells
  are shown again after a compaction; finished ones are not `[P]`.
- Check: the carry template has a line for restart commands. Tool: `tools/sweep_timer.sh <minutes>
  <command>` ends with `RE-ARM: <command line>`, so the completion notice carries the next round.

**R-loop-16. Orientation after a compaction is one script.** After a compaction the coordinator
reads the rules digest once and runs one script. The script prints the hints lines newer than the
digest header, the head of the state file, the live lanes, the tail of the duties log, and the size
of each capped file against its cap.
- Why: the orientation commands sat as prose in the instructions file and were copied by hand at
  every compaction. The digest went 58 bytes over its cap, and that was noticed only at the next
  sweep `[I]`. A cap is checked where the file is written and where it is read.
- Check: the after-compaction hook names the script. Tool: `tools/orient.sh` (file names set at its
  top or from the environment); it prints `OK` or `OVER` per capped file and always exits 0.

**R-loop-17. An agent whose deliverable is a report writes the report file first.** A consultant,
advisor or reviewer creates its report file with an outline right after orientation and fills it
as it goes. A stop at any point then leaves a usable report. Its agent type gets
`"deliverable": "report"` in the context guard's `agent_types`, so the guard's notices say "write
your report file now", not "update HANDOFF and commit". Limit: 200k, wrap-up from 170k, for a
report-only consultant on the strongest model (R-ctx-3 table).
- Why: in one planning round, 2 of 5 consultants reached the 150k lane limit before their report
  was finished. One was raised to 190k by hand; one stopped at 142k with sources unread. An earlier
  one had needed 200k `[M]`. The report is the whole deliverable, so a stop before it is pure loss.
  The guard's notices asked for a HANDOFF block and a commit, which a read-only agent has not got
  `[O]`.
- Check: the consultant definition (STARTER-KIT section 6) says "report file first"; the guard
  config marks its type as a report type.

**R-loop-18. A completion notice for a lane that is already closed: check its last activity, do
not read its output.** The lane ledger records when each lane was closed. Run
`tools/agent_scan.py --agent <id> --since <closing time>`; one call says whether the agent did
anything since.
- Why: after a compaction, a notice came back for an agent that had finished hours before. The
  coordinator read its output to rule out new work `[I]`.
- Check: the lane ledger has a `closed` column (STARTER-KIT section 2.5).

## 7. Bookkeeping

| artifact | purpose | updated by | format |
|---|---|---|---|
| agent notes file | state, findings, failed attempts, live jobs, HANDOFF on top | each agent, at every milestone | free bullets; HANDOFF <= 10k |
| hints file | facts and decisions the coordinator forgot before | coordinator, same turn | FACT/WHY/CHECK, USER lines verbatim |
| duties registry | each recurring instruction -> its enforcement | coordinator, same turn as the instruction | table in the hints file |
| findings register | every finding with owner/next/due/status | coordinator, same turn | 8-cell table row |
| resume sheet | STATE / SWEEP / STALE lines; agent id -> name, branch, notes, relaunch commands | coordinator | one line per entry |
| quality log | per finished agent and per merge: model, CI first pass, later fixes, cost, ctx | bookkeeping script | table row |
| dropped-levers list | ideas tried and dropped, with "revive if" | agents report, coordinator records | table row |
| orchestration log | hourly row: quota, burn, $/h, live agents, slots, alerts, action | coordinator | table row |
| dashboard state | status rows, trains, goal metric, quota | bookkeeping script + importers | JSON lines |
| ledger | merges/trains: commits, evidence ids, verdicts | merge steward | JSON lines |
| lane ledger | one row per lane: agent id, state (`live`/`closed`), opened, closed (time from `date`) | coordinator, at launch and at close | table row; a late notice for a closed lane is checked against its closing time (R-loop-18) |

**R-book-1. One bookkeeping script per event** (`launch`, `finish`, `push`, `note`) that updates all
state files at once.
- Why: hand bookkeeping fell behind under message load (rows missing for ~34 agents) `[M]`, and it
  was a measurable part of coordinator spend `[M]`.

**R-book-2. Findings register: every finding in the same turn, with owner, next step and due
time. A row closes only with a commit sha or job id.**
- Why: findings lived in prose and carry blocks with no owner and were lost `[U][I]`.
- Check: the duty checker flags unowned rows after 120 min and past-due rows; after each push,
  close rows whose id appears in commit messages (`grep -E "F<n>([^0-9]|$)"`; `\b` does not work in
  git's ERE `[P]`). Reports end with a "Follow-ups:" list, one line each with evidence.

**R-book-3. Hints file protocol.** An append-only file of facts and verbatim user decisions, plus
a short rules digest that is re-read after every compaction. See STARTER-KIT for the template.

- Each entry: `FACT <date>: <fact> / WHY: <incident> / CHECK: <enforcement or "hint only: reason">`.
- User decisions: `USER <date time>: "<verbatim quote>" -> <what it means>`.
- Keep a RULES DIGEST next to it (hard cap 15,000 bytes, 400 bytes per line): every user decision and
  standing rule still in force, one line each with its hints line number, and a
  `covers through line N` header. Results, status words and decision chains stay in the hints
  file; rules that only some actions need move to topic files read by a trigger line. A digest
  that had grown to ~31k tokens was more than a third of the post-compaction context `[M]`. After a
  compaction read the digest plus newer hints lines, not the whole file (R-loop-6) `[U][M]`. (Was:
  read the whole file; that stopped scaling at ~1,000 lines.)
- Regenerate the digest every ~50 new hints lines or when a user decision supersedes a digest line:
  read only the new lines, edit in place, bump the header. Every new user decision goes into BOTH
  files in the same turn; a decision missing from the digest is a lost rule `[U]`.
- Hints are cited by line number, so the file is append-only; a superseded entry gets
  `SUPERSEDED <date>: <by what>` appended to its own line `[I]`.
- Before changing an existing rule, grep its rationale first `[I]` (the 590 s wait regression).

**R-book-4. Append to one-line config files only through a normalizer.**
- Why: an ignore-list file gained a trailing newline; a later `printf ',key' >>` created a key with
  an embedded newline and silently unmuted two alerts `[I]`.
- Check: the normalizer rewrites the whole file; check `grep -c ''` = 1.

**R-book-5. Validate config edits through the reader's own code path.**
- Why: per-agent context overrides written next to (not inside) the tool's map were ignored for
  2 h `[I]`; a malformed override (string instead of number) was written `[I]`.

**R-book-6. Resumability.** Every agent commits WIP per milestone (<= 45 min) and keeps a
STATE/NEXT section with live job ids; the coordinator keeps the resume sheet; must-happen actions
(e.g. destroying rented compute at a deadline) run as detached processes, not session tasks `[U]`.

## 8. Monitoring

| monitor | what | trigger | output |
|---|---|---|---|
| alert daemon | hosts, slots held vs used, queues, timeouts, disk, memory, agent transcripts (wakes, context, stuck), usage windows | loop every 2 min | alerts JSONL + digest |
| wait-alert watcher | blocks until a new alert not in the ignore list | coordinator background task | exits -> harness wakes the coordinator |
| context guard hook (`hooks/context_guard.py`) | context notices at soft/firm, repeated past the limit; refuses the tool calls of a lane 20k past its limit; tells the coordinator which lanes are over; denies foreground waits > 270 s; reminds about bg jobs | every tool call (Pre/PostToolUse) | notice in the agent's context; JSONL log |
| Bash guard hook (`hooks/bash_guard.py`) | rejects `cd`/`pushd` and the commands listed in its config | every Bash call (PreToolUse) | reason in the agent's context |
| duty checker | OK/OVERDUE per duty from evidence files | sweep timer | stdout in the sweep notification |
| agent scan (`tools/agent_scan.py`) | SILENT/NOMSG/POLLING/CTX/OVER/HUNG/REWRITES per agent | sweep | stdout |
| context-efficiency | per agent: ctx at first report, 30-min ramp, avg ctx/call, handoff size | sweep | JSONL trend + flags |
| token mix | spend by token type and role | sweep | stdout (MIX part) |
| efficiency analysis | per agent / window: cost, wakes (gaps > 300 s), polls vs work, goal share | on demand | stdout |
| bottleneck scan | WAIT/IDLE-HELD/UNDERUSED/FULL from queue status | sweep | stdout |
| quota logger + pace | usage snapshots every 10 min; burn and projected run-out vs pace line | timer / daemon | JSONL + pace file; alert at deviation |
| dashboard | one status page for the user: goal metric, hosts, holders, agents, quota | always on | HTTP page |
| laptop guard | memory, swap, disk of the user's machine; kills own runaway processes | daemon | log |
| env scan | queued jobs missing required client env | sweep / on demand | stdout |

**R-mon-1. Automatic monitoring with a wake-up path.** The coordinator keeps a wait-alert process
in the background so a new alert wakes it.
- Why: every incident class was first caught by hand before the monitor existed `[I]`; the monitor
  then caught an OOM (one process at 33.5 GB), memory pressure and a 90-min hung agent `[I]`.
- Check: the duty checker verifies the daemon's output is fresh.

**R-mon-2. Act on alerts or mute them with a reason and an expiry.** Never mute a resource alert
(disk) for more than 30 min.
- Why: an ignored starvation alert preceded a queue deadlock `[I]`; a disk alert muted for an hour
  hid a fall from 40 G to 16 G free and every job was then blocked `[I]`.
- Check: ignore list is versioned with dates; revert condition: > 50 % of alerts not acted on.

**R-mon-3. Alert on absolute free space and own footprint, not percent used**, and on projected
quota run-out, not hourly cost (hourly cost alerts were noise with ~10 agents) `[M]`.

**R-mon-4. Restart a daemon after every change it reads; smoke-test after every infra deploy.**
- Why: a merged monitor change took effect only after the daemon restart `[I]`; a deploy took all
  host probes down for 7 min (argv > 8 KiB socket buffer; laptop tests used fake hosts) `[I]`; a
  subcommand broke while unit tests passed, and no A/B ran for 1.5 h `[I]`.
- Check: after every infra push, `queue status` (all hosts ok) and a dry run of each subcommand
  before restarting daemons; on failure revert to the previous tip at once.

**R-mon-5. Token pacing to the quota target.** A pace line ends at 100 % AT the reset. Behind pace
-> launch work at once. Throttle only if projected empty > 2 h before the user can reset, and then
by reducing concurrency, never by pausing everything `[U]`.
- Why: a night throttle at 86 % plus handoffs without successors burned ~0.5 %/h; the user judged
  that far too low `[M][U]`. Later the user ranked efficiency (progress per token) above pacing:
  running dry early is acceptable `[U]`.
- Check: a pace watcher wakes the coordinator at +3/-4 percentage points, at 90/100 %, and hourly.
  Each wake writes one orchestration-log row.

**R-mon-6. Never act on one estimator.** Cross-check the pace estimate with the raw slope over
>= 3 h and the dashboard's run-out.
- Why: a burn estimate over 60-120 min of whole-percent samples read 1.3-1.4 %/h against a real
  2.0-2.4 %/h; the coordinator added agents and the quota ran out 6 h early `[M]`.

**R-mon-7. Measure cost from transcripts, deduplicated by message id.** One API call is logged as
several blocks; naive sums double-count `[M]` (an early cost figure was 73 % too high).

**R-mon-8. Every timing or merge job failure is a warning with the log tail**; the coordinator
checks the main baseline job each sweep until its summary exists `[I]` (a death went unseen for
15 min).

**R-mon-9. Alerts about other owners' jobs never wake the coordinator.** On shared hosts, route
report-only alert kinds (idle slots, long job, unowned process) for jobs whose owner is not yours
to a digest file, not to the wake channel. Mute by OWNER, not by job id; per-id mutes are a stopgap
until the monitor filters by owner. Alerts on your own jobs keep waking.
- Why: three wakes in one afternoon for a co-tenant's idle slots, ~270k cache reads each, with
  nothing the coordinator was allowed to do about them `[M]`.
- Check: the ignore list has owner-scoped entries; revert if a co-tenant issue starves own jobs
  unseen.

## 9. Compute and job discipline

**R-job-1. One job queue for all compute.** Placement by need, slots per job, priorities with
aging (e.g. merge > dev > explore, +1 level per 15 min), reservations for big jobs, timeouts that
kill the remote tree, infra-failure retries, status and history.
- Why: manual routing put one host at load 21 while the other idled `[M]`; a fixed default host
  moved the bottleneck (15/15 with 6 waiters while the other had 10 free) `[M]`.

**R-job-2. Submit detached, own the job, wait by id.** `QUEUE_AGENT=<me> queue run --detach ...`
prints the id; no shell death can cancel it; only `queue cancel <id>` stops it.

**R-job-3. Hold slots per running process; never hold slots while waiting on another job.**
- Why: ~16 jobs from 8 agents waited 34 min at load 2.6/16 `[M]`; a job held 8 slots
  sleep-polling for a file no job produced `[I]`; a nested slot request deadlocked on its own outer
  lease `[I]`; a 48-slot job used ~1 core while 13 jobs queued `[M]`.
- Check: request ~1.2x the measured cores; serial loops never > 8 slots; an agent's cap counts all
  its jobs; idle-held alert.

**R-job-4. Evidence freshness: commit, then submit; freeze the tree until the job STARTS.**
- The queue syncs the worktree when the job starts, not at submit. A dirty flag records HEAD and a
  hash of the diff; the queue refuses a job whose source HEAD moved.
- Why: a WIP commit made after submit was tested instead `[I]`; "commit the notes now" while a job
  was queued failed that job at start `[I]` (3+ cases); a proxy submit synced a worktree mid-rebase
  `[I]`.
- Check: briefs: never commit, amend, switch branch or edit tracked files while a job from that
  worktree is queued; a proxy submit checks `git status --short` is empty in the same command.

**R-job-5. Back-dating.** When a job must be resubmitted (or bumped for the critical path), the
coordinator back-dates its submit time under the queue lock with a note, so it keeps its place.
Check the job's pins and env in the same step `[I]`.

**R-job-6. Distinct remote dir per concurrent job from one worktree.**
- Why: the second job's sync deleted the first job's base tree `[I]`.

**R-job-7. Job commands propagate the exit code.** Never end with `; echo rc=$?`. A harness that
evaluates nothing exits non-zero. Verify within minutes that a submitted job really runs.
- Why: a gates run exited 0 on a usage error and was reported "running" `[I]`; a push was gated on
  `(tests | tail) && git push`, so tail's exit code passed failing tests `[I]`.

**R-job-8. Cleanups only via allowlist scripts.** Directory deletions on shared hosts go through a
script that knows owners (successor chain) and refuses dirs named in queued jobs. Never prune by
idle time or name patterns alone. No `rm` with an unchecked variable path.
- Why: a portability script wiped every agent's build dir `[I]`; the coordinator's own emergency
  prunes broke a train job and archived live agents' predecessor worktrees `[I]`; a substring match
  removed another agent's inputs `[I]`; `rm -f "$P"/*` triggered a confirmation popup for the user
  `[U]`.
- Check: cleanup jobs use the queue's cleanup flag; agents remove only paths under their own dir.

**R-job-9. Disk and memory guards.** Compare scripts delete raw outputs of matching pairs at once
(keep mismatches and logs); never pull raw trees to the laptop; a worktree output dir > 2 GB is an
incident; temp files that may outlive a kill are created unlinked; size every dir the queue pulls
back before archiving anything.
- Why: one shared host fell to 6 GiB free three times in a day (raws, build caches, private
  copies) `[M]`; a laptop fell to 2.3 GiB free from 134 worktrees (66 G) `[M]`; killed runs left
  multi-GB spill files that were pulled back (50 GB) `[M]`; reading a multi-GB file whole caused a
  kernel panic `[I]`.
- Check: disk alerts with trend and top dirs; a per-agent disk limit; a write guard that stops a
  job writing > 2x its declared disk.

**R-job-10. The user's machine stays quiet.** Laptop builds via an admission wrapper (1 at a time,
waits for free memory, limited jobs, background QoS); daemons reniced; on a noise complaint check
`ps` for the cause `[U]` (the cause was idle queue clients spinning in Python, ~1.25 cores `[M]`).

**R-job-11. Edit running scripts atomically** (temp file + `mv`). Bash reads scripts while running
them `[P][I]`. Use `#!/usr/bin/env bash`, or start job scripts as `bash x.sh`, on hosts without `/bin/bash`
`[I]`. A job's helper scripts are tracked in its own tree, never referenced in another lane's
scratch dir `[I]` (a whole rerun round lost).

**R-job-12. Per-agent ssh multiplexing.** One shared master hits the server's session limit; no
multiplexing trips connection throttles (~50 % resets) `[M]`.

**R-job-13. Guard and action in one command.** A state check minutes before an action is stale
`[I]` (a deletion ran inside a timing lease opened 3 min after the check).

**R-job-14. Reference hosts and tool versions are calibrated before they produce data**, with
numeric pass bands `[I]` (a calibration failed in 20 min and saved a night of wrong references).

**R-job-15. Record a host's boot mode before a reboot and diff it after** `[I]` (a reboot fell to a
default mode with different memory limits, misdiagnosed first).

**R-job-16. Rented GPUs: stage the data first.** Upload each round's inputs to the provider's
network volume through the cheapest CPU-only instance long before the GPU round starts. Create
the GPU instance only when work can start, never to wait for an upload.
- Why: a 6.9 GB upload at ~3 MB/s took ~50 min and became the critical path, with a GPU rented
  to wait for it `[M]`.

## 10. Evidence and measurement discipline

**R-ev-1. Every number names its commit, scope and metric.** Full run vs slice, report id,
host and metric. Older numbers are labelled.
- Why: a number from an older timing file was quoted against a newer card `[I]`.

**R-ev-2. Judge levers by the real metric, not a proxy.** Use cheap deterministic proxies
(instruction counts, counters) to explore; confirm on the real metric with the cheapest A/B on the
target case before any full evidence run. Drop the lever if the real metric is within the noise
band.
- Why: a proxy said -3.8 %, the real metric said +1.2 % `[M]`; a layout change rated better by the
  proxy was 38 % slower on the real hardware `[M]`; four modelled gains measured at 0.4-0.8x of the
  model or refuted `[M]` (rank modelled gains at half).

**R-ev-3. Report the counter the change is meant to move**, not only time.
- Why: a fix expected to give ~20 % gave 3 %; the counter it should have changed was unchanged,
  which revealed a second trigger; fixing that cut the phase from 158 s to 0.44 s `[M]`.

**R-ev-4. Know your noise floors and bars.** Measure interference: on one host <= 2 busy slots kept
single-thread timing within 0.5 %, cross-job drift was 0.3-1.3 %, moving other jobs to efficiency
cores was not neutral (1.1-1.4 %) `[M]`. So A/B inside one job or one exclusive window. Identical
builds can swing 3-6 % from code placement `[M]`. A bar tighter than the floor cannot be met by
any change.

**R-ev-5. Check what an expensive verification step measures.** A measurement setting that forced a
compile on every case dominated the proxy count (567G vs 60G instructions) and made runs 2-3 h; it
also diluted every lever ~2x `[M]`.

**R-ev-6. Base provenance.** Base trees are built from `git archive <sha>` inside the job or in a
synced dir (never in a dir the queue does not sync). Each copied tree gets its own build-target
dir.
- Why: `rsync -a` kept old mtimes and the build reused stale fingerprints: green tests on old code
  `[I]` (twice). A shared target dir across copied trees runs stale code `[I]`.
- Check: touch changed sources after syncing; check the build log names the changed module.

**R-ev-7. Cumulative end-to-end checks per batch, not only per-step checks.**
- Why: every step of a long merge chain passed its own per-step bar (each within its
  small tolerance or granted an exception), but the whole chain, measured end to end against the
  original base, lost ~19 % (geomean 0.812, +23 % instructions) `[M]`. Small per-step costs and
  per-step exceptions compound; per-step bars measured against the previous step never see it.
- Check: after every N merged steps (and before any release), run the full metric of the chain tip
  against the last released base; keep a per-step attribution table (case x step) so a regression
  can be bisected to a step. Exceptions are granted against the cumulative budget, not per step.

**R-ev-8. Validate once.** Feature lanes prove READY with targeted evidence (red/green tests, one
release suite, lint, the cases they target, an expected-differ list). The merge steward runs the
full evidence once for the stack.
- Why: lanes running their own full suites booked the shared host for hours and the train repeated
  the same runs `[M]`. A behaviour change still needs the full test suite before READY: a lane that
  ran only its own modules missed a regression the end-to-end tests caught `[I]`.

**R-ev-9. Two merge lanes.** FAST lane = changes proven output-identical: rebase, full tests,
identity check (stacked by the steward), proxy A/B, real-metric A/B. TRAIN lane = behaviour
changes: full gates, small batches (2-3 changes only when files are disjoint).
- Why: one batch took 4 h+ with 3 drops, each drop rerunning the whole batch; gates add nothing for
  identical outputs `[M]`.
- Check: start a train at 3 ready branches or every 2 h `[U]` (nothing landed for 3 h with ~7
  branches ready `[M]`). READY = lint on the commits REBASED onto the current chain tip `[I]`.

**R-ev-10. Gates must be able to fail.** Canary cases for each silent-wrong class, a mutation check
that known-bad builds fail, every heavy or failing case gets at least one gate row `[I]`.

**R-ev-11. Speed claims only for runs that agree with the reference** `[I]`.

**R-ev-12. A slow test that covers a guard is never skipped for changes to that guard** `[I]`.

**R-ev-13. Settle tool-vs-reference disagreements with a windowed comparison plus a
"which mechanism could legitimately cause this" question** in one lean lane `[M]` (174k / 34 min
vs a predecessor's 206k without a verdict).

**R-ev-14. Independent review for silent-failure code** (cache keys, schedulers, generators) `[I]`
(9 silent bugs in one cache; 5 real issues in a scheduler review).

**R-ev-15. Check a verdict before acting on it.** Re-run a newly-bad verdict on a fresh build
(binary hash plus build time, its own build dir) before launching work on it. Classify a
PASS-to-WARN flip as a metric change or a behaviour change first.
- Why: a stale binary cost a lane round; a false premise started lanes twice `[I]`.

**R-ev-16. The release gate is a check of the exact release artifact on the real device.**
Install the signed build and run the checks before publishing. "Quit" in a check is the app's
real quit, never a kill signal. A document that ships inside a binary is part of the build: a
doc fix means rebuild, re-sign and grep the binary for the new text.
- Why: more than 1,100 green tests, yet 5 failures on the device; a false changelog claim was
  already embedded in a signed build `[M]`.

**R-ev-17. A data rebuild is READY only when every downstream consumer reads the new version.**
Ship every view the consumers use and run one consumer cross-check before READY.
- Why: a scorer still read moved items from a cache: -0.04 on the main metric, caught by chance
  `[I]`.

**R-ev-18. ML projects: audit and clean the labels before the first model lane.** Place every
label by the raw signal, find duplicate or overlapping sources, and measure each machine step
between the human's act and the label.
- Why: about two days of model rounds ran on misplaced and duplicated labels `[I]`.

**R-ev-19. ML rounds: one lane for compute, a separate lane for scoring and analysis**, each
with a minimum read set.
- Why: four successive lanes hit 130k before producing a number, because prep, launch tooling
  and scorer fixes sat in one lane `[M]`.

## 11. User interaction

**R-user-1. Record decisions verbatim**, with date and time, in the hints file, the same turn.
Every standing instruction also gets a duties-registry row with its enforcement `[U]`.

**R-user-2. Ask with a recommendation.** Use a structured question (AskUserQuestion) with options,
one marked "(Recommended)", each with its cost and consequence. Work continues on other items while
the question is open `[U]` (35+ such answers recorded; the user mostly picks the recommendation).

**R-user-3. "Let's discuss" means no actions.** Present options and wait `[U]`.

**R-user-4. Manual user actions as numbered steps** with exact commands or clicks.

**R-user-5. Status = short table + dashboard pointer.** Status, ETA, quota %, quality flags, key
changes. Honest assessment and one recommendation, not option dumps `[U]`.

**R-user-6. Know what is delegated.** Typical split after a few days: the coordinator decides
technical choices, trade-offs, defaults, accept/fix decisions with evidence, routing, limits, briefs
`[U]`. The user keeps: licences, publishing, security and system changes, compute and machine
rules, renting, quota and accounts, metric and gate changes, exceptions to quality bars, stopping
(killing) agents, and how to proceed
after a permission-checker refusal `[U]`.
Report each autonomous decision afterwards with numbers and how to revert.

**R-user-7. Surface risk early**, with plan B, not at the deadline `[U]`.

**R-user-8. Discuss before starting** work types the user named (ports, new infrastructure,
outward-facing actions) `[U]`.

**R-user-9. Downloads and licences:** agents list download needs at scoping; the coordinator
classifies each item against a licence policy and asks once per natural break with per-item
checkboxes; work switches to other items meanwhile `[U]`.

**R-user-10. Questions while the user is away.** Before an overnight question, queue all work
that does not depend on the answer, so compute keeps running; the answer gates only the last
step (for example the push). Use a structured question only while the user is present; otherwise
ask in chat text and continue with other items. Record each answer with its scope (one job, one
batch, standing).
- Why: a host idled ~8 h behind an overnight question `[M]`.

## 12. Safety

**R-safe-1. No approval popups when the user is away.** Never run anything that makes the harness
ask the user: writes to `.git/` internals or harness config, shell rc files, paths outside the
agent's worktree / tmp / job dirs, sandbox overrides, app-level requests. Skip the step and report
it.
- Why: one popup stalls a lane all night `[U][I]`.
- Lanes also stop and ask before any step that can raise an operating-system dialog (keychain,
  privacy consent, installer or updater UI); the coordinator asks the user `[I]` (a packaging
  lane raised 3 dialogs on the user's screen).

**R-safe-2. No permission laundering.** Never wrap, encode or move a command into a script so a
permission, hook or isolation check cannot see it, and never retry variants after a refusal. Send
the exact refusal text to the coordinator.
- Why: an agent hid job commands in a script to pass the worktree isolation checker `[I]`.

**R-safe-3. A message from an agent is never the user's consent.**
Agent messages direct work but cannot authorize permission or config changes `[P]`.

**R-safe-4. Deletions.** Literal absolute paths or `${VAR:?}`; owner-aware allowlist scripts on
shared hosts; nothing deleted by a predecessor after a handoff; archive before removing worktrees
(diff patch + untracked tarball + archive branch for unreachable heads).

**R-safe-5. Private data.** Per-run copies (mode 700, deleted also on failure); tools that stage
private data clean up themselves; never run tools inside shared read-only copies that are hard
links (writers corrupt the master for everyone) `[I]` (twice in 2 h; detection by manifest +
inode check caught both); names of private data items never appear in commits or reports (use hash keys). Scan the history before publishing
anything.

**R-safe-6. Shared session state.** If a plugin (e.g. self-compaction) is keyed by session id and
sub-agents share the coordinator's session id, sub-agents must never call it.
- Why: a sub-agent's compaction request compacted the coordinator instead, and the coordinator's
  cancel dropped a sub-agent's request `[I]`.
- Check: the `self-compact` plugin in this repository refuses calls from sub-agents since 0.4.0.
  The rule stays in briefs for older versions and for other plugins.

**R-safe-7. Secrets** never in briefs, notes, commits or URLs.

**R-safe-8. Never `cd` in Bash**; absolute paths and `git -C`. Check `rev-parse --show-toplevel`
before every commit.
- Why: a coordinator `cd` moved a later sub-agent's start dir `[I]`; a removed-and-recreated
  worktree let a commit land on the main checkout `[I]`.
- Check: a PreToolUse Bash guard hook (`hooks/bash_guard.py`) rejects `cd`/`pushd` and heavy local builds (quote-aware,
  fails open on parse errors).

**R-safe-9. Agents do not create task suggestions in the user's interface.** Follow-ups go into reports; the coordinator launches agents
`[U]`.

**R-safe-10. Every report has a Safety section**: guards, bounds, what could silently differ, what
was delegated and whether it was spot-checked `[O]`.

**R-safe-11. No history rewrite or reset in a shared checkout while lanes work there.**
`git filter-repo`, `reset --hard`, `checkout`/`switch` and stash-less rebases in the
coordinator's checkout discard uncommitted edits of lanes that write into it. Before any of
these: `git status --short`, then wait or stash.
- Why: a history rewrite hard-reset the main checkout and destroyed a lane's uncommitted edits;
  they were recovered only by replaying its transcript `[I]`.
- Check: prevent the need with a pre-commit hook that blocks content that must not be committed, so nothing has
  to be rewritten after a push.

## 13. Failure modes catalogue

| failure | symptom | generalized lesson | rule |
|---|---|---|---|
| wake storm | spend spikes, many big cache writes | finished/handed-off agents with live waiters or job clients get woken | R-ctx-1, R-ho-1, R-ho-5 |
| fat successor | 150-250k within 30 min | successors read whole notes and logs | R-ctx-5 |
| setup handoff | agent hands off with no result | soft limit below brief + setup cost | R-ctx-3 |
| trigger overrun | handoff 30-50k past the trigger | "one more wait" after the trigger | R-ho-1 |
| rule decay after compaction | a rule "simplified" into its expensive opposite | rules without their reason; not re-reading hints | R-book-3, R-wait-1 |
| duty drift | recurring duties silently skipped | duties in memory only | R-loop-2 |
| zombie finding | open finding already fixed on main | registers not closed on push | R-book-2, R-brief-4 |
| lost notes | notes vanish with an auto-cleaned worktree | untracked files die with the worktree | R-ho-7 |
| stale build | green tests on old code | mtime-preserving copies, shared target dirs | R-ev-6 |
| moved HEAD | job refused or tests the wrong commit | queue syncs at start, not submit | R-job-4 |
| false green | rc 0 with nothing evaluated | piped or echoed exit codes | R-job-7 |
| hold-and-wait | slots held, 0 cores used, waiters pile up | inputs first, then slots | R-job-3 |
| booked != used | full queue, idle cores | over-requested slots, elastic tails | R-loop-4 |
| priority inversion | merge jobs wait behind filler | backfill not capped while merges queue | R-loop-4 |
| disk emergency | all jobs blocked | raws kept, caches, private copies; muted alerts | R-job-9, R-mon-2 |
| self-inflicted prune | coordinator cleanup breaks live work | prune by owner chain, not idle time | R-job-8 |
| proxy mismatch | lever wins on proxy, loses on real metric | confirm on the real metric early | R-ev-2 |
| death by a thousand cuts | each step passes, the chain loses ~19 % | cumulative end-to-end checks + per-step attribution | R-ev-7 |
| hung tool call | status "running", no activity | scan for tool_use without result | R-loop-5 |
| placeholder brief | literal `$(RULES)` sent | prompts are not shell-expanded; lint | R-brief-3 |
| permission laundering | checks bypassed via scripts | report refusals verbatim | R-safe-2 |
| night popup | lane stalled for hours | no actions that ask the user while away | R-safe-1 |
| estimator error | wrong pace decision | cross-check two estimators | R-mon-6 |
| idle quota | burn far below pace overnight | successor at every handoff; don't over-throttle | R-mon-5, R-ho-6 |
| goal drift | cheap tokens, little progress | progress per token; role shares + goal delta per sweep | §1.2 |
| duplicated work | two agents commit the same fix | re-aim on landing | R-loop-10 |
| duplicated evidence | lanes and train run the same full suites | validate once | R-ev-8 |
| shared-plugin collision | sub-agent compacts the coordinator | session-keyed tools are coordinator-only | R-safe-6 |
| hard-link corruption | master data rewritten by a run | never run inside shared copies | R-safe-5 |
| shell expansion surprise | backticks run inside a heredoc; `=word` expanded by zsh | quote heredoc delimiters (`<<'EOF'`); quote `=` args | R-job-11 |
| missing client env | job waits the full queue, then fails at once | briefs name required env vars; scan the queue for them | R-brief-1, §8 env scan |
| hints file outgrows context | coordinator at ~266k right after a compaction | rules digest + grep, no full read | R-book-3, R-loop-6 |
| co-tenant alert wakes | expensive wakes with no allowed action | mute by owner, digest file | R-mon-9 |
| one check per call | 100+ calls in a lane's first 20 min | batch independent checks | R-ctx-10 |
| stale stopgap | a config workaround outlives its reason and kills later jobs | every stopgap gets a hints line with its watch condition and an undo trigger | R-loop-4 |
| lane dies mid-step | handoff rule at 200k, lanes run out at 132-150k | one limit per lane with a wrap-up point before it, rolling handoff | R-ctx-3 |
| heavy start | every fan-out agent starts at ~63k | lean agent file | R-ctx-11 |
| zombie predecessor | a closed lane commits in its successor's worktree | "LANE CLOSED" in the launch message | R-ho-9 |
| orienting successor | 48 calls of re-reading, one commit | no-orientation successor briefs | R-ho-10 |
| bulk-run crash | ~50 concurrent workflow runs, session and runs lost | cap concurrency, estimate tokens first | R-ctx-14 |
| image flood | context spent on full-size screenshots | image budget in the brief | R-ctx-13 |
| rewritten checkout | a history rewrite destroys a lane's uncommitted edits | no rewrite or reset while lanes work there | R-safe-11 |
| "0 tests" misread | a test runner prints one "Executed N" line per bundle, most of them 0 | briefs name the bundle or summary line to read | R-brief-1 |
| rule text in the wrong file | rules found only in an evidence table nobody reads | one file holds the text of every rule | §14 |
| lost restart command | a finished timer's notice arrives after a compaction; its command is in neither summary nor carry | the job prints its own restart command; the carry lists it | R-loop-15 |
| hand-copied orientation | a capped file runs over its cap unnoticed until the next sweep | one orientation script that also checks caps | R-loop-16 |
| report never written | a consultant hits its limit with sources unread and no report | report file first, report-type guard notices, higher limit | R-loop-17 |
| re-reading a closed lane | a late notice makes the coordinator read a finished agent's output | check activity since the closing time in one call | R-loop-18 |

## 14. Governance of the playbook itself

- **One file holds the text of every rule.** Evidence, metrics and review dates may live in a
  registry file; rule text must not live only there. After one split into rules, evidence and
  changelog files, 34 rules were found only in registry rows that nothing told the coordinator
  to read `[I]`. Keep the rules file small enough to load in one read.
- **Rule registry.** Each rule: evidence (numbers, dates), expected effect, metric, review date,
  revert condition. A rule that reaches its review date without data is a removal candidate.
- **Change discipline.** One orchestration change at a time per area (routing, phase size,
  exploration, merging, effort, monitoring), staggered so effects can be attributed. Log
  confounders. Use a cheap control group where possible. Report sample sizes; label estimates.
- **Reversibility.** For each change: where it lives and how to turn it off. Mark "no off switch".
- **Changelog.** Date, incident, measured impact, rule. Keep the old value so it can be reverted.
- **Upkeep.** Update the playbook in the same turn as the practice changes `[U]`. Re-evaluate the
  goal metric itself periodically, like the playbook (metric changes need the user) `[U]`.
- **Periodic audit.** A fresh worker reads the data and the playbook only and proposes changes; the
  first audit dropped three rules and added three `[I]`.
