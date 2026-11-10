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
