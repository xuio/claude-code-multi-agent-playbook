# Multi-agent playbook for Claude Code

Field-tested rules for running one coordinator and many parallel sub-agents on real coding
projects with [Claude Code](https://claude.com/claude-code), with the measurement or incident
behind each rule.

It covers the questions that come up once you run more than a handful of agents at a time:

- How large may an agent's context grow, and when do you hand off to a fresh agent?
- How do you wait for long jobs without paying for prompt-cache misses?
- Which model does which work, and at which reasoning effort?
- What goes into a brief, a handoff note and a successor's brief?
- How do ten lanes merge without running every test suite ten times?
- How does the coordinator keep its rules and the user's decisions across compactions?

## What we measured

From our own transcripts and job logs, on Opus-class and Haiku-class models, October and
November 2026:

| finding | number |
|---|---|
| Share of spend that is cache reads (the context, re-read on every call) | 60–70 % |
| Cost per call by context size (n = 20,548 calls) | $0.06 below 100k, $0.18 at 200–300k, $0.45 above 400k |
| First-turn context of a general-purpose sub-agent vs a lean agent file | ~63k vs ~8k tokens |
| Share of spend in one 2.7-hour window caused by 26 wake-ups of big-context agents | 44 % |
| Cost of one cache miss at 200k context | about 12 warm polls |
| Lanes that were told to hand off at 200k | all 7 ran out at 132–150k |
| Token-saver habits (search first, read line ranges, tail logs) | about −47 % tokens per call |

The short version: **context size is the cost.** Keep every agent small, hand off early with a
written note, and never wake a big agent for work a fresh one can do.

## What is in the repository

| file | what it is |
|---|---|
| [RULES.md](RULES.md) | Every rule in one line (about 25 KB). This is the file a coordinator agent loads. |
| [PLAYBOOK.md](PLAYBOOK.md) | All 139 rules with their reason, evidence and check, plus a catalogue of failure modes. |
| [STARTER-KIT.md](STARTER-KIT.md) | Day-1 setup for a new project: file templates, the rules block for briefs, brief and handoff templates, checklists. |
| [examples/agents/](examples/agents) | Agent definition files: engineer, worker, reader, an `Explore` override and a lean batch worker for fan-outs. |
| [examples/skills/token-saver/](examples/skills/token-saver) | A minimal skill with the reading habits that keep contexts small. The example agent files preload it. |
| [examples/scripts/](examples/scripts) | `bgrun.sh` and `bgwait.sh`: run a long command in the background and poll it without losing the prompt cache. |
| [hooks/](hooks/README.md) | Claude Code hooks that enforce the rules agents forget: a context guard (tells each agent how full its context is, denies waits that outlive the prompt cache), a command that changes context limits while agents run, a Bash guard (no `cd`, no heavy local builds) and a reminder after every compaction. |
| [plugins/self-compact/](plugins/self-compact/README.md) | A plugin that lets the coordinator compact its own context at a point it chooses, carry text verbatim across the compaction and resume. |
| [tools/agent_scan.py](tools/agent_scan.py) | One line per agent of a running session: context against its limit, idle time, polling, hung tool calls, cache rewrites. |
| [tools/make_rules_digest.py](tools/make_rules_digest.py) | Regenerates RULES.md from PLAYBOOK.md. |

## Quick start

1. Clone the repository next to your Claude Code configuration:

   ```bash
   git clone https://github.com/xuio/claude-code-multi-agent-playbook.git ~/.claude/playbooks/multi-agent
   ```

2. Point Claude Code at it. Add this to `~/.claude/CLAUDE.md`, or to a project's `CLAUDE.md`:

   ```markdown
   # Multi-agent work
   When coordinating parallel sub-agents, follow ~/.claude/playbooks/multi-agent/RULES.md.
   Open the matching section of PLAYBOOK.md in the same folder when a rule needs its reason or its check.
   ```

3. Copy the agent files you need from `examples/agents/` into your project's `.claude/agents/`
   and restart the session. Agent files load at session start only.

4. Install the hooks ([hooks/README.md](hooks/README.md)). Rules that live only in a prompt are
   forgotten after a compaction; the hooks keep the two most expensive ones in force (context
   limits and the 270 s wait cap).

5. For a project that will run for days, work through [STARTER-KIT.md](STARTER-KIT.md) once:
   it sets up the notes, hints and findings files, the hooks and the brief templates.

## Ten rules to start with

1. **One context limit per lane.** 150k by default, 80k for many short tasks, 160k for long,
   complex work. The coordinator may raise or lower it when it sees the signals. (R-ctx-3)
2. **Fan-outs use a lean agent file.** It starts at about 8k tokens; a general-purpose agent
   starts at about 63k. (R-ctx-11)
3. **Fresh agent per task.** Never resume an agent that has been idle for more than five
   minutes; its cache is gone. (R-ctx-1, R-ctx-2)
4. **Wait in foreground polls of at most 240 seconds.** For longer waits the coordinator ends
   its turn, and a lane hands off. (R-wait-1)
5. **Log to a file, look at a view.** Tool output is paid again on every later call. (R-ctx-6)
6. **A numeric call budget in every brief.** (R-brief-9)
7. **A rolling handoff block on top of every lane's notes.** The successor reads only that and
   the passages its next step needs. (R-ho-1, R-ctx-5, R-ho-10)
8. **Two models.** The strongest for judgment and code, the cheapest for bounded reading.
   (R-route-1)
9. **Validate once.** Lanes prove READY with targeted evidence; the merge train runs the full
   evidence once. (R-ev-8, R-ev-9)
10. **Every rule needs an enforcement, and the coordinator re-reads a short rules digest after
    every compaction.** (R-loop-6, R-book-3)

## How to read the evidence

Each rule in the playbook carries a tag:

- `[M]` measured: numbers from transcripts, monitors or job logs.
- `[I]` incident: one or a few concrete cases. Real, but not a rate.
- `[U]` a user's decision or preference. Check whether it fits you.
- `[P]` platform fact: how the harness, the cache or the tools behave.
- `[O]` opinion or reasoning, not measured.

The numbers come from particular projects, models and prices. Treat them as starting points and
measure your own. Limits such as 150k are defaults, and R-ctx-3 says when to change them. The
compute rules in section 9 assume a shared job queue over several build hosts. With one machine,
read `queue run` and `queue wait` as the `bgrun.sh` and `bgwait.sh` helpers in `examples/scripts/`.

## Contributing

Issues and pull requests are welcome, most of all with measurements that confirm or contradict
a rule: context sizes, cost per call, handoff counts, time lost to an incident. Say what you
measured and on how many agents. After editing PLAYBOOK.md, run
`python3 tools/make_rules_digest.py`. Tests: `python3 -m unittest discover -s hooks/tests`,
`python3 -m unittest discover -s tools/tests`, and `claude plugin test plugins/self-compact`.

## Licence

[MIT No Attribution](LICENSE) (MIT-0). Copy, adapt and reuse anything here, with or without credit.

This project is not affiliated with Anthropic. Claude and Claude Code are trademarks of Anthropic.
