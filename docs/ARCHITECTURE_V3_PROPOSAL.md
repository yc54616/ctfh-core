# CTFH V3 Modular Architecture Proposal

Date: 2026-10-06
Status: implemented baseline (Phases 0-9); final blocker recheck READY by GPT-6.1 Sol and GPT-6 Astra. Claude Opus 5.5 architecture review was incorporated; final patch recheck was unavailable because the local Claude CLI was logged out.
Research basis: docs/research/MODULAR_AGENT_ARCHITECTURE_2026-10-06.md
Review synthesis: docs/reviews/v3/SYNTHESIS.md

## Implementation status

The phased implementation described below has been completed through the offline learning/promotion substrate. The current implementation includes two real native AgentAdapters, attempt-owned PTY and GhostChrome services, pinned read-only skills and frozen knowledge, sealed trace/CAS publication, and a separate `ctfh_learning` package. Final blocker rechecks by GPT-6.1 Sol and GPT-6 Astra returned READY with no blockers after lifecycle, artifact, trace-coverage, and learning-provenance fixes.

The original Claude Opus 5.5 architecture review materially shaped the design. A final patch-level Opus recheck could not be rerun because the local Claude CLI was not authenticated at final validation time.

## 1. Decision

V3 is not a ground-up agent runtime rewrite.

V3 is a thin amendment to the existing CTFH architecture that adds better environment capabilities, skill/knowledge pinning, and an offline learning plane while preserving the native agent and the existing control-plane boundaries.

The human-readable conceptual model remains:

Brain / Memory / Skills / Hands / Tools / Context

The enforceable runtime boundaries remain:

Kernel / TaskSource / TargetProvider / WorkerEnvironment / AgentAdapter / OutcomeAuthority / Artifact and Trace Storage

The conceptual model explains where capabilities belong. It does not imply one mandatory core protocol per concept.

## 2. One-sentence architecture

CTFH admits a pinned Attempt consisting of an AgentAdapter, a WorkerEnvironment with a static admitted capability set, pinned skills and optional pinned knowledge, and a Target; the native agent performs the solve loop, OutcomeAuthority independently verifies results, and offline learning converts sealed evidence into new versioned knowledge and skill artifacts.

## 3. Corrected top-level model

~~~
                    USER / EVENT / CTF TASK
                              |
                              v
                    Intent / Governance
                    admission-time only
                              |
                              v
+--------------------------------------------------------------+
|                        CTFH KERNEL                           |
|                                                              |
| KernelLoop / single writer / state machines / scheduler      |
| RunLock / deadlines / reservations / recovery / journal      |
| candidate control events / authoritative outcome commit      |
+---------+----------------------+----------------------+--------+
          |                      |                      |
          v                      v                      v
     TaskSource            TargetProvider        OutcomeAuthority
                                 |
                                 | target identity / epoch
                                 v
                        WorkerEnvironment
                 one attempt-owned kill domain
                   /           |            \
                  /            |             \
         native tools    session service    browser service
                         PTY/GDB/remote      lazy, managed
                  \            |             /
                   \           |            /
                          AgentAdapter
                    native Codex / Claude
                              |
                              v
                     native agent solve loop
                              |
                   candidate control event
                              |
                              v
                       OutcomeAuthority
                              |
                              v
                     KernelLoop CAS commit

Bulk trace and artifacts:
native stream / session output / browser observations
                    |
                    v
          attempt-private append-only output
                    |
                    v
            seal / hash / publish to CAS

Offline only:
sealed evidence -> learning-side wiki -> candidate snapshot/skill
               -> evaluation -> promote/reject -> versioned digest
~~~

## 4. Core contracts

### 4.1 KernelLoop

KernelLoop remains the only authoritative writer.

It owns:
- task state
- attempt state
- admission
- deadlines
- reservations
- KernelEpoch
- restart recovery
- pure Strategy execution
- RunLock resolution
- narrow external-effect journal
- candidate/outcome control handling
- authoritative task outcome commit

It does not own:
- model turns
- tool routing
- browser observations
- PTY traffic
- skill activation
- memory retrieval
- context compaction

No ordinary tool call requires a KernelLoop round trip.

### 4.2 TaskSource

TaskSource returns frozen, content-addressed task specifications and evaluator references.

It does not own agent or environment lifecycle.

### 4.3 TargetProvider

TargetProvider owns the challenged target lifecycle and TargetEpoch when CTFH controls the target.

It owns:
- provision/start
- health/reconcile
- endpoint description
- reset
- TargetEpoch
- destroy

Target resources use TargetOwnerTag and are separate from WorkerOwnerTag.

Worker cancellation must never implicitly reset or destroy a still-valid target.

A target reset invalidates attempts bound to the old TargetEpoch. For unmediated traffic, stale access is prevented by quiescing or cancelling old holders before replacement becomes usable; a database integer alone cannot revoke already-open I/O.

### 4.4 WorkerEnvironmentProvider

WorkerEnvironment is the attempt resource-lifetime and kill-domain boundary.

It owns:
- workspace
- filesystem isolation
- process namespace or equivalent kill domain
- network policy
- installed security tools
- per-attempt native config home
- PTY/debugger/remote sessions
- managed browser service
- optional future desktop service
- hard kill
- cleanup
- WorkerOwnerTag sweep

Kernel decides when the environment exists.
WorkerEnvironmentProvider performs the mechanics.

Required properties:
- bounded/cancellable creation
- no resource may escape ownership before it is attributable to WorkerOwnerTag
- hard kill must terminate the full kill domain
- destroy is idempotent
- owner-scoped orphan sweep exists
- capacity is not released until execution resources are confirmed gone

### 4.5 AgentAdapter

AgentAdapter preserves native agent behavior.

It owns:
- effective native-agent configuration
- launch
- cancellation cooperation
- native configuration materialization
- result/candidate extraction
- usage/latency observation when available
- trace coverage declaration
- adapter-specific exposure of environment capabilities

Conceptual contract:

~~~
capabilities()
effective_configuration()
materialize(brief, skill_set, knowledge_snapshot, admitted_caps) -> native_config
run(AttemptRequest, Env, AttemptSink, CancelToken) -> AttemptResult
~~~

For native agents, AgentAdapter does not reimplement:
- model planning
- model conversation loop
- native tool selection
- native skill activation
- native context compaction

A future custom-API adapter may internally contain its own context compiler and tool catalog, but those remain private to that adapter.

### 4.6 OutcomeAuthority

OutcomeAuthority is the only verification boundary.

Worker text such as solved is never authoritative.

OutcomeAuthority declares:
- authoritative or advisory
- local or external
- verification protocol/version

Only authoritative PASS can allow KernelLoop to commit Task -> SOLVED.

For external effects, preserve the existing journal semantics:
- intent persisted before dispatch
- idempotency or reconcile semantics
- UNKNOWN never blindly retried
- candidate serialization per task

OutcomeAuthority returns verdict/evidence. KernelLoop performs the actual state mutation.

### 4.7 Artifact and trace storage

Control state and bulk evidence remain separate.

Attempt output is written directly to attempt-private storage.

Examples:
- native agent stream
- session transcripts
- browser observations
- artifacts
- candidate records
- timing metrics

Bulk data never flows through SQLite or the KernelLoop inbox.

At attempt end:
- execution resources are stopped
- output is sealed
- coverage/completeness is recorded
- immutable artifacts are hashed
- selected artifacts are published to content-addressed storage
- references are committed to control state

Native agents may hide some raw tool output. Record trace coverage truthfully instead of pretending the trace is complete.

## 5. Conceptual capability model

The following concepts remain useful for reasoning and documentation:

### Brain

Brain means the decision-making native agent.

For Codex/Claude Code, Brain is inside AgentAdapter and is not a separate CTFH runtime service.

### Context

Context means the model-visible projection.

For native agents, CTFH only prepares launch-time inputs and native config through materialize(). Per-turn compaction and conversation context remain native.

A full ContextCompiler is deferred until a custom API agent actually needs it.

### Tools

A tool is an invocation capability, not necessarily transactional or retry-safe.

A timeout does not imply rollback. Side effects are classified by the real execution boundary.

Core does not own a global ToolRouter.

### Hands

Hand remains a useful name for stateful interaction capabilities such as terminal session, browser, or desktop.

In initial V3, these live as services inside WorkerEnvironment and are not universal core plugins.

### Memory

Memory means reusable derived information across runs.

Initial live execution uses no mutable shared memory.

### Skills

Skills are versioned procedural packages, using the Agent Skills format where possible.

They are profile content, not kernel authority.

## 6. Capability admission

V3 does not use a universal lease subsystem for attempt-private resources.

Admission is computed once:

admitted = requested intersection policy intersection available

Rules:
- admission only narrows
- no skill may grant itself a capability
- no memory snapshot may expand capabilities
- adapter compatibility may reject a profile
- unsupported hard guarantees cause admission failure
- the admitted set is pinned in RunLock

Scarce resources are reserved at admission even if their service process starts lazily.

Capabilities that live inside the environment are revoked by terminating the environment kill domain.

Shared resources outside the environment are deferred. If later needed, they get a dedicated broker whose lease is enforced at the actual I/O boundary.

## 7. Interactive session service

The highest-priority new execution capability is interactive terminal/session support motivated by EnIGMA-style evidence.

It belongs inside WorkerEnvironment.

Minimum API:

~~~
open(argv, cwd) -> session_id
send(session_id, data)
read(session_id, since_offset, max_bytes, wait_ms | until_quiescent)
signal(session_id, signal)
close(session_id)
list()
~~~

Requirements:
- attempt-scoped session IDs
- no crash-resume guarantee
- bounded session count
- bounded transcript bytes
- bounded disk usage
- offset-based reads
- blocking/waiting read option to avoid busy polling
- process state separate from read timeout
- PTY mode may merge stdout/stderr
- raw transcript written directly to attempt output
- all session processes inside environment kill domain

Debugger REPLs and remote interactive sockets initially reuse this session abstraction rather than becoming separate DebuggerHand or RemoteSessionHand protocols.

### Exposure to native agents

Benchmark at least:
- CLI on PATH plus a skill
- MCP-over-stdio service inside the environment

The kernel is not on that path.

Do not replace native shell/file tools with CTFH duplicates unless matched tests show a benefit.

## 8. Browser service

Browser is an optional WorkerEnvironment capability.

Rules:
- backend/configuration admitted before attempt launch
- scarce memory/capacity reserved at admission
- browser process starts lazily on first use
- one lifecycle owner per attempt
- profile state is disposable attempt state by default
- no cross-attempt reuse
- downloads go to attempt artifacts
- authenticated state never silently migrates between backends
- one default observation representation first
- screenshot on demand
- additional DOM/accessibility/mixed modes are separate benchmark treatments

GhostChrome is the first intended managed backend.

browser-use may be evaluated as a backend/session implementation, but its full agent framework must not become a second agent runtime inside CTFH.

A real user browser/profile is deferred and denied by default.

If ever required:
- explicit per-run authorization
- external dedicated broker
- lease enforced at broker I/O boundary
- attempt cancellation never blindly kills the user's browser

## 9. Native skill integration

Use Agent Skills-compatible packages.

A skill set is selected before launch and content-addressed as a whole:
- SKILL.md
- scripts
- references
- assets
- optional CTFH metadata

Skill metadata may:
- declare compatibility
- declare requirements that narrow/reject admission
- describe expected tools

Skill metadata cannot grant permissions.

allowed-tools semantics must be validated per native client because some clients may treat them as pre-approval.

AgentAdapter.materialize places the selected skills into the isolated per-attempt native skill location.

Native progressive disclosure remains native.

Skill scripts execute under WorkerEnvironment restrictions like any other code.

No live SkillRuntime or CTFH activate_skill tool is required initially.

## 10. Memory and knowledge

Initial modes:

- memory=off
- memory=frozen
- memory=live deferred

### memory=off

All native persistent memory/index state is disabled or isolated in the per-attempt native config home.

Prior browser history and previous attempt indexes are not inherited.

### memory=frozen

The attempt sees a read-only, content-addressed knowledge snapshot.

RunLock records:
- snapshot digest
- retrieval/configuration mode
- any native indexing configuration

The acting agent cannot modify the shared snapshot.

### learning-side wiki versus agent-visible snapshot

The learning-side wiki persists:
- patterns
- rejected hypotheses
- evolution logs
- skill-impact history
- evaluation statistics

It is not directly visible to acting agents.

Agent-visible knowledge is a curated export with its own digest and promotion evidence.

Therefore, WikiSkill's rule that the wiki persists after skill rollback does not mean rejected knowledge automatically influences future live runs.

## 11. Offline learning and skill evolution

Learning is a separate process/package.

It reads sealed artifacts and writes new versioned artifacts.

Pipeline:

~~~
sealed traces
 -> trace analysis
 -> learning-side wiki revision
 -> candidate knowledge or one atomic skill change
 -> held-out evaluation
 -> promote or reject
 -> versioned snapshot / skill-set digest
~~~

Rules:
- no live competition mutation by default
- no evaluator secrets in learning inputs
- no OutcomeAuthority internals in worker-visible learning data
- source-trace tasks and promotion-evaluation tasks do not overlap
- final untouched evaluation set is distinct from the repeated development/promotion set
- promotion records provenance, source traces, evaluator version, performance and latency
- rollback means future runs re-pin a previous digest
- prior run evidence is never rewritten
- learning jobs do not compete with live-event provider quota or scarce runtime resources by default

## 12. RunLock additions

Extend existing RunLock rather than inventing a second RuntimeProfile.

Record:
- AgentAdapter ID/version/digest
- effective model and reasoning settings
- WorkerEnvironment template digest
- installed tool/service versions
- admitted capability set
- session service version
- browser backend/profile policy if enabled
- skill-set digest
- memory mode
- knowledge snapshot digest if used
- native memory/index configuration
- IntentBrief/contract identity
- OutcomeAuthority protocol/version
- trace coverage declaration
- benchmark strategy parameters

RunLock is immutable for an attempt.

## 13. Control events versus trace records

Kernel control events are bounded and small.

Examples:
- AttemptStarted
- EnvironmentReady
- CandidateSubmitted
- AttemptFinished
- AttemptCancellationRequested
- AttemptCancelled
- AttemptLost
- OutcomeVerified

Trace records are append-only and bypass KernelLoop:
- native model/tool stream where observable
- session calls and output
- browser observations/actions
- adapter diagnostics
- coverage gaps

There is no mandatory global event bus.

## 14. Cancellation and restart

Preserve and extend the existing cancellation barrier.

Correct ordering:
1. commit CANCELLING
2. stop further lazy acquisitions
3. trip CancelToken
4. wait for in-flight environment/service provisioning to become terminal
5. hard-kill established environment execution resources
6. confirm the kill domain is empty
7. perform final WorkerOwnerTag sweep
8. only then release execution capacity
9. mark CANCELLED
10. slower non-capacity cleanup may continue under the reaper

Lazy browser/session creation is subject to the same barrier.

On kernel restart:
- acquire host lock
- increment KernelEpoch
- mark prior live attempts LOST
- sweep WorkerOwnerTag resources
- reconcile TargetProvider state
- reconcile journaled external operations
- let Strategy decide again

Terminal/browser sessions are not transparently resumed across crashes.

## 15. Preserved V2 invariants

V3 must explicitly preserve I1-I14 from the current architecture.

At minimum:
- one authoritative writer
- committed transition before dispatch
- target resources never swept as worker resources
- target reset changes TargetEpoch
- STARTING cancellation barrier
- no DB transaction across external wait
- narrow external-effect journal
- bounded control inbox
- pure Strategy
- RunLock reproducibility
- immutable sealed evidence
- OutcomeAuthority-only success path
- truthful adapter enforcement declarations
- matched real-native benchmark gate

## 16. New V3 invariants

I15. Ordinary agent tool/observation traffic never reaches KernelLoop or SQLite; only bounded control events do.

I16. Every attempt-owned service process is inside the WorkerEnvironment kill domain and is gone before its execution capacity is released.

I17. Admission can only narrow the requested capability set and cannot expand it during an attempt.

I18. Every skill set and knowledge snapshot visible to an attempt is content-addressed, pinned in RunLock and read-only to the attempt.

I19. Learning inputs exclude evaluator secrets; source-trace tasks and promotion-evaluation tasks do not overlap.

I20. ctfh_core imports no implementation package or learning package.

## 17. Performance fast path

Default:

~~~
TaskSpec + IntentBrief
 -> resolve RunLock once
 -> admission once
 -> warm WorkerEnvironment
 -> AgentAdapter.materialize
 -> launch native agent
 -> native loop on native tools
 -> optional in-environment session/browser service only if needed
 -> candidate control event
 -> OutcomeAuthority
 -> KernelLoop CAS commit
 -> seal/publish evidence
~~~

No mandatory:
- per-turn CTFH context compilation
- ToolRouter hop
- memory retrieval
- lease renewal
- duplicate file/shell tools
- extra model call
- eager browser startup
- learning activity

Bulk hashing/sealing must not stall KernelLoop.

Candidate verification should begin promptly without waiting for unrelated slow cleanup.

## 18. Benchmark design

Every added capability is one treatment.

Required sequence:

1. Direct native vs thin supervised, matched model/prompts/tools/native config.
2. Thin supervised vs +interactive session service.
3. Baseline vs +hand-written skill set.
4. Baseline vs +managed browser on browser-required tasks.
5. Baseline vs +frozen knowledge snapshot.
6. Offline skill/wiki evolution only after previous treatments earn promotion.

Do not compare direct native against V3 plus TerminalHand as one step; that mixes supervisor overhead with changed tool semantics.

Metrics:
- verified solve rate
- completion by deadline
- time to authoritative PASS
- first valid candidate latency
- queue wait
- environment startup
- native agent startup
- first service startup
- per-session/browser call latency
- verification time
- capacity reclamation
- cancellation latency
- leaks/orphans
- tokens/cost when observable
- added schema/context tokens
- cold and warm runs

Declare numeric overhead and regression budgets before promotion.

## 19. Revised implementation order

Phase 0: pass the existing real-native I14 gate.

Phase 1: document I1-I20 mapping and add fault tests. No package restructure.

Phase 2: extend RunLock and static capability admission.

Phase 3: implement interactive session service inside WorkerEnvironment and benchmark it.

Phase 4: integrate hand-written Agent Skills through AgentAdapter.materialize and benchmark them.

Phase 5: add a second real native AgentAdapter; only then extract any additional shared adapter abstraction justified by both.

Phase 6: add GhostChrome managed browser service inside WorkerEnvironment and benchmark it.

Phase 7: formalize trace schemas and trace-coverage labels.

Phase 8: introduce a small read-only frozen knowledge snapshot and benchmark it.

Phase 9: add offline learning-side wiki and skill proposal/evaluation.

Phase 10: promote additional abstractions only when a second implementation or measured benefit requires them.

## 20. Deferred

Do not implement initially:
- core Hand protocol
- universal Hand leases or generation renewal
- global ToolRegistry/ToolRouter
- core MemoryProvider
- live memory writes
- vector/graph/A-MEM/Letta live integration
- full ContextCompiler
- DesktopHand or GUI grounding
- real-user-profile browser
- full browser-use agent framework
- planner/executor overlays
- critics on the authoritative path
- model routing
- heterogeneous multi-agent delegation
- learned SkillWeaver-style tool APIs
- dynamic plugin discovery
- generic module dependency resolver
- typed debugger tools beyond the generic session service

## 21. Source-layout direction

Do not reorganize the repository before behavior proves the boundaries.

When implementation pressure justifies extraction, preserve this dependency direction:

~~~
ctfh_core
  kernel/control types
  TaskSource protocol
  TargetProvider protocol
  WorkerEnvironmentProvider protocol
  AgentAdapter protocol
  OutcomeAuthority protocol
  artifact/trace contracts
  governance/intent

implementation packages
  local environment and session service
  native agent adapters
  browser service
  task/target providers

ctfh_learning
  offline trace analysis
  wiki maintainer
  candidate skill/knowledge generation
  evaluation and promotion
~~~

ctfh_core never imports implementation or learning packages.

## 22. Final architecture position

The research was useful because it exposed missing concepts: interactive sessions, portable skills, persistent knowledge, and skill evolution.

The multi-model review showed that these concepts should not all become new CTFH runtime layers.

The final direction is therefore:

- keep CTFH thin
- preserve native-agent ownership of its own loop
- put attempt-owned interaction capabilities under WorkerEnvironment
- pin skills and knowledge through AgentAdapter materialization
- keep KernelLoop off the ordinary tool path
- keep OutcomeAuthority independent
- keep learning offline
- generalize protocols only after real replacement pressure or benchmark evidence

This is the V3 architecture to implement.
