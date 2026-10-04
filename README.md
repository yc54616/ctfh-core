# ctfh-core

Performance-first thin supervisor for authorized CTF competition agents.

## Design rule

CTFH does **not** replace Codex, Claude Code, or future native agents with another
generic agent framework.

It supervises them:

```
Task
  -> Target
  -> ready WorkerEnvironment
  -> native AgentAdapter
  -> candidate
  -> authoritative OutcomeAuthority
  -> result
```

The kernel owns lifecycle, deadlines, process cleanup, durable metadata and
measurement. The native agent owns reasoning and its ordinary tool loop.

## Why

The final architecture was reviewed against 2025-2026 CTF-agent research and
independently critiqued by:

- GPT-6 Sol (xhigh)
- GPT-6 Astra (xhigh)
- Claude Opus 5.5 (xhigh, domain-neutral systems review)

The reviewers strongly converged on:

1. benchmark the whole deployed harness, not just the model
2. environment/tool readiness materially changes solve rate
3. keep v0 thin and run a real native agent immediately
4. do not assume planner/executor or mixed-model routing helps
5. process isolation/kill/sweep matters more than generic lease abstractions
6. verify outcomes independently from worker self-report
7. every extra layer must earn its place in paired wall-clock/solve-rate tests

## Documents

- `docs/ARCHITECTURE.md` — accepted v2 design
- `docs/RESEARCH.md` — papers, repositories, services and measured findings
- `docs/DECISIONS.md` — review consensus and rejected alternatives
- `docs/reviews/` — model review outputs
- `benchmarks/README.md` — evaluation rules
- `profiles/baseline.yaml` — first vertical-slice target profile

## v0

v0 is not complete until a real pinned native agent completes independently
verified tasks through CTFH and we can compare:

- direct native-agent runtime
- the same native agent through CTFH

The wrapper must measure and justify its own overhead.

Planned first adapter: Codex CLI.
Planned first strategy: DirectStrategy.
Planned first environment: local isolated process environment, followed by a
ready Kali/Docker environment.

## Deferred by design

Not v0:

- planner/executor
- heterogeneous model routing
- pass@k
- Jev
- context-mode integration
- codebase-memory
- Agent Skills packs
- generic package plugin discovery
- Redis / distributed scheduling
- another ReAct framework

These become modules only after paired benchmarks show a gain.
