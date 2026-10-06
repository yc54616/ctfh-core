# CTFH V3 Review Synthesis

Date: 2026-10-06

Reviewers:
- GPT-6.1 Sol (xhigh)
- GPT-6 Astra (xhigh)
- Claude Opus 5.5 (max)

All three reviewers independently returned NEEDS-CHANGES.

## Consensus

The research taxonomy is useful, but it must not become a second agent runtime inside CTFH.

Keep these as the actual core runtime contracts:
- KernelLoop
- TaskSource
- TargetProvider
- WorkerEnvironmentProvider
- AgentAdapter
- OutcomeAuthority
- Artifact/trace storage

Brain, Context, Tools, Skills, Memory, and Hands remain useful conceptual categories. They are not all mandatory core protocols.

### Native agent boundary

The native agent remains a complete deployed system behind AgentAdapter.

Native agents keep ownership of:
- model turns and planning
- native tool selection
- native skill activation
- conversation history
- context compaction

A future custom-API AgentAdapter may implement its own context/tool loop internally.

For native adapters, replace the proposed core ContextCompiler with a one-time materialization step:

AgentAdapter.materialize(brief, skill_set, knowledge_snapshot, admitted_caps) -> native config

Materialization runs before launch, uses a per-attempt isolated native config home, mounts or writes admitted skills and knowledge, is hashed into RunLock, performs no hidden model calls, and does not rewrite context per turn.

### Resource lifetime boundary

WorkerEnvironment remains the attempt resource-lifetime and kill-domain boundary.

Attempt-owned resources live under it:
- workspace and filesystem isolation
- terminal, PTY, debugger and remote interactive sessions
- managed browser session
- desktop/GUI session if later added
- helper and service processes

Kernel decides when to create or terminate an environment. WorkerEnvironmentProvider implements bounded create, kill, destroy and owner-scoped sweep.

No universal Hand lease or generation-fencing subsystem is added initially. A generation number cannot revoke an unmediated process handle or socket. Shared resources outside the attempt environment may later use a dedicated broker only when the broker can enforce the lease at the real I/O boundary.

### Capability admission

Capability admission is static for an attempt and only narrows:

admitted = requested intersection policy intersection available

Skill metadata, memory configuration and adapter compatibility may drop a capability or reject the profile. They never expand the admitted set during a run.

The resolved capability set is frozen in RunLock. Scarce capabilities are reserved at admission, even if their processes start lazily.

### Tools and Hands

There is no mandatory global ToolRouter or ToolRegistry on the native hot path.

Ownership is hybrid:
- core owns capability names and records admitted configuration
- WorkerEnvironment owns executable capability implementations
- AgentAdapter exposes them through the native agent's supported integration mechanism

Hands remain implementation vocabulary for stateful session capabilities. They are not a universal core ABI yet.

Interactive terminal support remains the highest-priority new execution capability, implemented as a WorkerEnvironment session service.

Minimum session service:
- open(argv, cwd)
- send(session_id, data)
- read(session_id, since_offset, max_bytes, wait_ms or until_quiescent)
- signal(session_id, signal)
- close(session_id)
- list()

Session IDs are scoped to one attempt. They persist across agent turns, not across crashes.

Raw transcripts go directly to attempt output with byte/session/disk caps. PTY mode may merge stdout and stderr and must not promise separate streams.

For native agents, compare two exposure mechanisms:
- CLI on PATH plus a skill
- MCP over stdio inside the environment

The kernel is not on the tool-call path.

### Browser

Browser is admitted up front and started lazily inside WorkerEnvironment.

Default rules:
- exactly one lifecycle owner per attempt
- profile data is disposable attempt state
- downloads go to attempt artifacts
- no cross-attempt browser reuse
- one default observation representation first; additional modes are benchmark treatments
- no silent migration of authentication between browser backends

A borrowed real-user browser/profile is deferred. If ever needed, it is an external shared resource with a dedicated broker and explicit per-run authorization. Attempt cancellation must not blindly kill the user's browser.

### Control events versus trace records

KernelLoop receives only bounded control events: attempt lifecycle, candidate submission, terminal outcome, cancellation and fault signals.

Bulk trace data never flows through KernelLoop or SQLite. Tool calls, observations, PTY output, browser observations and native-agent stream details go directly to append-only per-attempt files with coverage metadata.

### Memory, Wiki and Skills

Keep the information-model separation:
- control state: authoritative execution state
- sealed trace: immutable evidence
- learning-side wiki: persistent analysis state, including rejected hypotheses and skill-impact history
- agent-visible knowledge snapshot: curated, content-addressed, read-only export
- skill set: versioned Agent Skills package

Do not expose the learning-side wiki directly to acting agents.

This resolves the WikiSkill rollback issue: the wiki can persist after a rejected skill edit, but rejected or unvalidated claims do not automatically become retrieval-visible knowledge.

The live path is read-only initially:
- memory=off is the default
- memory=frozen means a pinned immutable knowledge snapshot
- memory=live is deferred until reproducibility and A/B evidence exist

Native memory/index features are disabled or isolated when memory=off and their effective state is recorded in RunLock.

### Skills

Use Agent Skills as portable content, not as a new kernel subsystem.

At admission:
- select the skill set
- validate compatibility and allowed-tools semantics
- allow requirements only to narrow or reject the profile
- hash SKILL.md, scripts, references, assets and CTFH metadata together
- materialize into the native agent's isolated skill location

The native agent performs progressive disclosure where supported.

Skill scripts remain ordinary files executed under WorkerEnvironment restrictions. They are not automatically registered as privileged tools.

### Offline learning

Learning remains outside the live solve path.

Pipeline:
sealed traces -> learning-side wiki -> candidate knowledge/skill change -> held-out evaluation -> promote or reject -> versioned snapshot/digest

Requirements:
- source-trace tasks and promotion-evaluation tasks do not overlap
- evaluator secrets and OutcomeAuthority internals are excluded from learning inputs
- repeated promotion tuning cannot reuse the final untouched evaluation set
- every promoted snapshot or skill records provenance, evaluator version, metrics and configuration
- learning jobs do not share live-event provider quota or scarce runtime resources by default

### Retained correctness invariants

V3 is an amendment, not a replacement of the existing control-plane invariants.

Preserve:
- one KernelLoop and one authoritative writer
- no DB transaction over external awaits
- Task and Attempt state machines
- KernelEpoch and LOST recovery
- WorkerOwnerTag and TargetOwnerTag separation
- TargetEpoch freshness
- WorkerEnvironment cancellation barrier
- narrow external-effect journal and UNKNOWN reconciliation
- pure Strategy
- sealed artifact publication
- OutcomeAuthority as the only verification boundary
- RunLock and matched configuration
- real-native-agent performance gate

### New proposed invariants

I15: Ordinary agent tool/observation traffic never goes through KernelLoop or SQLite; only bounded control events do.

I16: Every attempt-owned service process is inside the WorkerEnvironment kill domain and is gone before the associated execution capacity is released.

I17: Admission can only narrow the requested capability set and cannot expand it during an attempt.

I18: Every skill set and knowledge snapshot visible to an attempt is content-addressed, pinned in RunLock and read-only to the attempt.

I19: Learning inputs exclude evaluator secrets; source-trace tasks and promotion-evaluation tasks do not overlap.

I20: ctfh_core imports no implementation package such as builtin adapters or learning code.

## Revised fast path

TaskSpec + IntentBrief
-> resolve immutable profile and RunLock
-> all-or-nothing admission
-> prepare WorkerEnvironment
-> AgentAdapter.materialize
-> launch pinned native agent
-> native loop using native tools plus only missing environment capabilities
-> candidate control event
-> OutcomeAuthority
-> KernelLoop compare-and-swap commit
-> seal and publish evidence

No mandatory per-turn CTFH ToolRouter, ContextCompiler, memory retrieval, lease renewal, schema translation or extra model call.

## Revised benchmark sequence

1. Direct native versus current thin supervision with matched agent-facing configuration.
2. Thin supervision versus thin supervision plus interactive session service.
3. Baseline versus hand-written skill set.
4. Baseline versus managed browser capability on browser-needed tasks.
5. Baseline versus frozen knowledge snapshot with strict memory isolation.
6. Offline Wiki/skill evolution only after prior treatments show value.

Each comparison changes one treatment at a time.

## Revised implementation order

0. Pass the existing real-native I14 gate before V3 implementation.
1. Preserve current architecture and add explicit invariant mapping. No package churn.
2. Extend RunLock with capability set, skill-set digest, memory mode/snapshot digest and environment service versions.
3. Add environment session service for PTY/debugger/remote interactive use; benchmark it.
4. Add hand-written Agent Skills through AgentAdapter.materialize with isolated native config home; benchmark them.
5. Add a second real native AgentAdapter and extract shared adapter protocol only from demonstrated common needs.
6. Add managed GhostChrome browser service inside WorkerEnvironment; benchmark only on tasks that need it.
7. Formalize trace schema and trace-coverage metadata.
8. Add a small read-only frozen knowledge snapshot as a treatment.
9. Add offline Wiki/skill evolution with independent promotion evaluation.
10. Reintroduce deferred abstractions only when a second real implementation or benchmark result justifies them.

## Deferred

Defer until triggered by evidence:
- universal Hand protocol in core
- universal leases and generation renewal
- core MemoryProvider
- graph/vector/Letta/A-MEM runtime integration
- live memory writes
- full ContextCompiler
- global ToolRegistry or ToolRouter
- DesktopHand and GUI grounding
- real-user-profile browser
- browser-use as an agent framework
- planner/executor, critic, model routing and delegation overlays
- dynamic plugin discovery
- learned tool APIs from SkillWeaver
- typed debugger tools beyond generic interactive sessions

## Final synthesis verdict

Keep the human-readable model:

Brain / Memory / Skills / Hands / Tools / Context

But implement V3 around the enforceable runtime boundaries:

Kernel / TaskSource / TargetProvider / WorkerEnvironment / AgentAdapter / OutcomeAuthority / Artifact and Trace Storage

with ctfh_learning fully offline.

V3 should be treated as a thin amendment to the existing architecture, not a ground-up agent-runtime rewrite.
