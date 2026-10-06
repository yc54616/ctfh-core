# ctfh-core

Performance-first thin supervisor for authorized CTF competition agents.

## Design rule

CTFH does **not** replace Codex, Claude Code, or another native agent with a
second general-purpose agent framework.

It supervises native agents through a small set of enforceable boundaries:

```text
TaskSource
   |
TargetProvider
   |
KernelLoop ---- OutcomeAuthority
   |
WorkerEnvironment
   |-- native shell/files
   |-- optional interactive PTY/session service
   |-- optional managed GhostChrome browser service
   |
AgentAdapter
   |-- Codex CLI
   `-- Claude Code CLI
```

The kernel owns durable state, deadlines, recovery, target epochs,
cancellation, RunLock configuration, artifact publication, and authoritative
outcome commits. Native agents keep their own planning, conversation loop,
ordinary tools, context compaction, and native skill activation.

## V3 implemented capabilities

### Agent adapters

- Codex CLI
- Claude Code CLI
- common `AgentAdapter` contract extracted only after both real adapters were
  exercised

### WorkerEnvironment capabilities

- Bubblewrap attempt isolation and kill-domain ownership
- interactive PTY sessions through `ctfh-session`
  - open / send / read / signal / close / list
  - bounded in-memory and on-disk transcripts
  - cancellation-safe connected RPC shutdown
- lazy managed GhostChrome browser through `ctfh-browser`
  - attempt-local disposable browser profile
  - explicit WebSocket attachment to the attempt-owned Chrome
  - navigation domain allowlist support
  - browser lifecycle and controller processes tracked under WorkerOwnerTag

Neither session nor browser calls go through KernelLoop or SQLite.

### Skills and native configuration

Codex skills are materialized into an attempt-local snapshot before launch.

- content-addressed digest in RunLock
- symlinks dereferenced into the snapshot
- materialized tree digest rechecked
- read-only inside the sandbox

The native Codex configuration is also materialized and pinned so host
MCP/plugin/memory settings do not silently alter a benchmark run.

### Frozen knowledge

`memory=off` is the default.

A run may optionally use a frozen knowledge snapshot:

- source tree is content-addressed
- copied into attempt-local storage
- digest rechecked
- mounted read-only at `/ctfh-knowledge`
- exposed as `$CTFH_KNOWLEDGE_DIR`
- pinned in RunLock

There are no live shared-memory writes on the solve path.

### Trace and artifacts

Bulk agent/session/browser evidence stays out of KernelLoop/SQLite.

At attempt completion:

1. environment services are quiesced
2. service evidence is copied to attempt output
3. `trace-manifest.json` records stream hashes, sizes, coverage boundaries,
   and truncation accounting
4. evidence is made read-only
5. supervised attempts are streamed into content-addressed storage without
   buffering the whole attempt in RAM

### Offline WikiSkill-style learning

`ctfh_learning` is a separate package; `ctfh_core` does not import it.

Pipeline:

```text
sealed trace
  -> verify evidence
  -> append-only learning wiki
  -> candidate knowledge / one atomic skill
  -> held-out baseline-vs-candidate evaluation
  -> promote or reject
  -> versioned read-only snapshot
```

Promotion safeguards include:

- source-trace tasks cannot overlap promotion tasks
- promotion tasks cannot overlap the final untouched set
- promotion/final splits are immutable once committed
- invalid first evaluations cannot poison the split registry
- evaluation records are bound to RunLock hashes
- candidate evaluations are bound to the exact candidate content digest
- candidate provenance fields are rehashed on load
- each candidate has one terminal decision; reject cannot later flip to promote
- rejected hypotheses remain in the learning-side wiki but are not
  automatically agent-visible

## Quick smoke tests

From the repository root:

```bash
PYTHONPATH=src python3 -m ctfh_core.cli smoke \
  --agent codex \
  --mode pair \
  --model gpt-6-sol \
  --effort high
```

Enable the interactive session capability:

```bash
PYTHONPATH=src python3 -m ctfh_core.cli smoke \
  --agent codex \
  --mode pair \
  --interactive-session
```

Enable the managed browser capability:

```bash
PYTHONPATH=src python3 -m ctfh_core.cli smoke \
  --agent codex \
  --mode pair \
  --browser \
  --browser-domain example.ctf
```

Use a frozen knowledge snapshot:

```bash
PYTHONPATH=src python3 -m ctfh_core.cli smoke \
  --agent codex \
  --mode pair \
  --knowledge-snapshot ./knowledge-snapshot
```

Claude can be selected with `--agent claude`; its default model is
`claude-opus-5-5`. A valid local Claude login is required.

## Offline learning CLI

The package exposes:

```text
ctfh-learn verify-trace
ctfh-learn wiki-add
ctfh-learn wiki-show
ctfh-learn propose
ctfh-learn decide
```

Promotion input is a restricted evaluation-summary schema. Raw evaluator
secrets are not accepted as learning input.

## Validation status

As of 2026-10-06:

- full repository suite: **101 passed**
- real Codex supervised smoke: authoritative PASS
- final supervised attempt artifact and trace manifest published to CAS
- GPT-6.1 Sol final blocker recheck: **READY**, blockers none
- GPT-6 Astra final blocker recheck: **READY**, blockers none
- Claude Opus 5.5 participated in the architecture review that shaped V3; its
  final patch recheck could not be rerun because the local Claude CLI was
  logged out at final validation time

## Documents

- `docs/ARCHITECTURE.md` — preserved control-plane architecture and invariants
- `docs/ARCHITECTURE_V3_PROPOSAL.md` — V3 design and implementation sequence
- `docs/research/MODULAR_AGENT_ARCHITECTURE_2026-10-06.md` — modular
  agent/browser/skills/memory research synthesis
- `docs/reviews/v3/CONSENSUS.md` — multi-model architecture consensus
- `docs/reviews/v3/SYNTHESIS.md` — corrected V3 boundary synthesis
- `benchmarks/README.md` — matched evaluation rules

## Still deferred

These remain deferred until paired benchmark evidence justifies them:

- core-level generic Hand protocol
- global ToolRouter / ToolRegistry
- live shared memory and vector/graph retrieval on the solve path
- Desktop/GUI hand
- real-user-profile browser reuse
- planner/executor overlays
- critic loops on the authoritative path
- heterogeneous model routing
- generic dynamic plugin discovery
- distributed scheduling
