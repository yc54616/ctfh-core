## VERDICT

**NEEDS-CHANGES**

V3’s conceptual distinctions are useful, but its proposed core interfaces would expand CTFH from a native-agent supervisor into an agent execution framework. Preserve V2’s control plane and introduce capabilities inside existing adapters and environments first.

The largest risks are unenforceable generation fencing, duplicated lifecycle ownership, replacement of native context/tool behavior, and benchmarking several changes as one treatment.

This review uses only the frozen bundle. Its source-tree inventory is empty, and the reported 54 passing tests do not establish V3 correctness or native-agent performance. [Bundle evidence](/home/yc54616/Desktop/ctfh-core/docs/reviews/v3/REVIEW_BUNDLE.md:2560)

## CRITICAL CHANGES BEFORE IMPLEMENTATION

1. **Keep the complete native agent behind AgentAdapter.** V3’s ContextCompiler consumes observations, trajectories, tool schemas, activated skills, and memory. That is much of an agent framework’s execution loop. For native agents, CTFH should assemble launch configuration and initial task input; the native agent should retain tool selection, skill activation, conversation management, and compaction. An optional custom API agent may implement these components internally. [ContextCompiler proposal](/home/yc54616/Desktop/ctfh-core/docs/reviews/v3/REVIEW_BUNDLE.md:699)

2. **Remove universal Hand leases from the initial kernel.** “Any action from a stale generation is rejected” requires an enforcement boundary covering every action. A native shell or retained browser connection can bypass a registry. V2 explicitly recognizes this limitation. Use private attempt environments and confirmed termination; introduce a specific broker only for a genuinely shared resource. Controlled target reset must stop old dependent workers, or enforce revocation at their actual I/O boundary, before exposing the replacement target. [V3 lease claim](/home/yc54616/Desktop/ctfh-core/docs/reviews/v3/REVIEW_BUNDLE.md:372), [V2 fencing limitation](/home/yc54616/Desktop/ctfh-core/docs/reviews/v3/REVIEW_BUNDLE.md:2156)

3. **Correct cancellation ordering and extend its barrier to lazy resource creation.** The existing sequence places a clean owner sweep before hard-killing established workers. Those workers can still create resources. The sequence should close admission, cancel and terminate outstanding provisioning, kill established execution resources, confirm termination, perform the final owner sweep, then release capacity and mark CANCELLED. Browser and PTY acquisition must obey the same barrier. Unconfirmed cleanup must retain or quarantine affected capacity. [Cancellation sequence](/home/yc54616/Desktop/ctfh-core/docs/reviews/v3/REVIEW_BUNDLE.md:2126)

4. **Separate capability availability, permission, requirements, and enforcement.** A skill is an artifact with requirements, not a permission capability. Tool-schema filtering is visibility control, not sandbox enforcement. Admission must check effective adapter/environment configuration and reject unsupported hard guarantees, including opaque request-level budgets. Lazy activation may use only the immutable RunLock’s declared envelope. Required scarce resources must be reserved together; optional acquisition must not introduce indefinite waiting while other reservations remain held.

5. **Preserve one authoritative writer and a narrow evidence path.** OutcomeAuthority returns a verdict and evidence; KernelLoop commits SOLVED. Session managers, adapters, and learning components receive no control-store write access. Freeze the candidate and required verification inputs before verification; seal the complete trace separately. Consequential external operations must traverse their journaled boundary—classification metadata alone cannot ensure that.

6. **Benchmark before extracting the execution surface.** “Interfaces only; old tests pass” is insufficient. The first gate must be a real native agent through the existing thin supervisor with matched configuration. A new TerminalHand changes tool semantics and belongs in a subsequent experiment. [Proposed implementation order](/home/yc54616/Desktop/ctfh-core/docs/reviews/v3/REVIEW_BUNDLE.md:1005)

## KEEP

- Single KernelLoop, single SQLite writer, committed transitions before dispatch, and no transaction across external waits.
- Independent OutcomeAuthority; authoritative PASS is the only route to SOLVED.
- Separate TargetProvider and WorkerEnvironment ownership, including separate owner tags and reapers.
- Immutable RunLock and truthful declarations of budget enforcement, usage visibility, and trace coverage.
- Attempt-private output, immutable published evidence, bounded control messages, and narrow external-effect journaling.
- Logical separation of execution state, evidence, reusable knowledge, procedural skills, and model-visible context.
- Offline learning, explicit construction, MCP at interoperability edges, and cheapest-sufficient capability selection.

## MERGE OR DELETE

| Proposed abstraction | Required disposition |
|---|---|
| Brain / BrainAdapter | Merge into the existing AgentAdapter boundary. “Brain” may remain descriptive vocabulary. |
| Separate Kernel / Runtime framework | Keep attempt supervision and scheduling together; avoid a second execution authority. |
| Core Hand protocol and universal leases | Move session controllers into WorkerEnvironment implementations. Delete universal leases from the initial design. |
| Global ToolRegistry / ToolRouter | Keep native tools native. Adapter-local tool composition is sufficient where integration requires it. |
| Core ContextCompiler / SkillRuntime | Move launch-input assembly and compatibility checks into AgentAdapter/profile preparation. Native agents retain disclosure and activation. |
| RuntimeProfile / universal ModuleManifest | Extend existing profile and RunLock records with concrete selected-component metadata. Avoid a dependency-resolution system. |
| TraceStore / ArtifactStore | Use one evidence-storage facility with append streams and sealed artifacts; distinguish their states explicitly. |
| KnowledgeWiki / MemoryProvider | Initially use one simple knowledge backend. Logical views do not require separate services. |
| Generic Verifier beside OutcomeAuthority | Keep OutcomeAuthority as the sole verification boundary. |
| Governance runtime | Keep intent and scope validation in admission/configuration; avoid another live execution loop. |

The proposed package tree is premature. Move files only when a demonstrated ownership boundary requires it.

## DEFER

- Shared authenticated-browser and desktop brokers until resource sharing is required.
- Generic Hand interoperability until multiple real implementations demonstrate a common contract.
- Vector/graph memory, Letta integration, autonomous memory revision, and live knowledge mutation.
- Learned browser macros registered as tools and automatic skill-script tool generation.
- Planner/executor overlays, critics, routing, heterogeneous delegation, and browser decision agents.
- Plugin discovery, universal dependency manifests, and distributed scheduling.
- Desktop grounding and GUI automation until a task requires them and their overhead is measured.

Offline skill evolution is a later experiment, not a prerequisite for modular supervision.

## CORRECTED MODULE BOUNDARIES

| Boundary | Responsibility | Implementation details that stay outside core |
|---|---|---|
| **CTFH kernel** | Durable task/attempt transitions, admission, deadlines, reservations, cancellation/recovery coordination, outcome commits | Model turns, tool routing, session observation, skill activation |
| **TaskSource** | Task identity, inputs, evaluator reference, event metadata | Agent and environment lifecycle |
| **TargetProvider** | Target provisioning, reset, reconciliation, TargetEpoch | Worker cleanup and native-agent restart |
| **WorkerEnvironmentProvider** | Execution containment, workspace, processes, resource attribution, termination and sweep | PTY/browser/GUI controllers, backend-specific session handling |
| **AgentAdapter** | Native launch, effective configuration, observable results, cancellation cooperation | Native context, tools, skills, memory integration and optional API-agent loop |
| **OutcomeAuthority** | Independent verification protocol and verdict evidence | Worker reasoning and authoritative DB writes |
| **AttemptSink / ArtifactStore** | Bounded result notifications, bulk evidence capture, immutable publication | A general event-processing framework |
| **Offline learning** | Evidence analysis, wiki revisions, skill proposals, evaluation and promotion artifacts | Live attempt state and current RunLock mutation |

Kernel owns the **decision to create or terminate an environment**; its provider owns the **mechanics and subordinate resources**. An adapter can use native session facilities without introducing kernel-visible Hand objects.

The existing AgentAdapter contract—capabilities, effective configuration, and cancellable run—is a sufficient starting surface. [Existing contract](/home/yc54616/Desktop/ctfh-core/docs/reviews/v3/REVIEW_BUNDLE.md:2011)

## HANDS / BROWSER / TERMINAL REVIEW

**Hands are useful implementation vocabulary, not yet justified core plugins.** Stateful session ownership differs from callable operations, but that distinction does not require a universal acquire/observe/reconcile/sweep interface.

**Terminal:** Preserve native terminal facilities when sufficient. Add PTY support inside the environment/adapter only for demonstrated gaps. Specify running versus exited state, output cursors, bounded reads, truncation, and cancellation. A read timeout does not mean process exit. PTYs commonly combine stdout and stderr, so the proposed observation shape cannot universally promise separate streams. Serialize conflicting operations on one session while allowing independent sessions.

EnIGMA’s bundled findings support usable interactive access; they do not establish that extracting all local execution into TerminalHand improves native agents.

**Browser:** Use one configured lifecycle owner and lazy startup. Managed attempt browsers belong under environment ownership. Borrowed real Chrome needs a different contract: ownership of a connection or selected tabs does not authorize killing Chrome, deleting its profile, or altering unrelated authenticated state. Backend fallback must preserve explicit ownership and configuration; silently migrating authentication is unacceptable.

A native agent creating an attempt-contained browser can satisfy lifecycle ownership. Requiring every creation to pass through a kernel lease adds unnecessary control traffic.

**Desktop:** Keep GUI session lifecycle and grounding inside the environment implementation. Screen coordinates and element references need observation-freshness handling there. Defer this path initially.

**Tools:** Replace “atomic typed operation” with “typed invocation.” A timeout can follow partial effects; neither rollback nor safe retry follows from a schema. Side-effect labels are descriptive and potentially argument-dependent.

Filesystem operations need no Hand.

## MEMORY / SKILLS / WIKISKILL REVIEW

Keep these distinctions without demanding separate runtime services:

- **Control state:** authoritative recovery information.
- **Trace/artifacts:** captured evidence, with explicit completeness and sealing status.
- **Wiki/memory:** derived, revisable knowledge.
- **Skills:** versioned procedural packages.
- **Context:** the agent’s current projection.

For native agents, Agent Skills should remain native packages. Hash their content and scripts into RunLock; let the adapter configure discovery. Requirements metadata checks compatibility and never grants permissions. Bundled scripts execute under the same environment restrictions as other code.

The proposed WikiSkill-style sequence is reasonable offline:

`sealed evidence → provenance-linked wiki revision → one candidate skill change → evaluation → versioned promotion or rejection`

**Wiki persistence after skill rejection needs qualification:** retain failure history and rejected hypotheses, but do not present rejected claims as established knowledge. Every promoted skill should identify its source traces, wiki revision, evaluation protocol, and results. Rollback changes future selection; it does not rewrite prior run evidence. [Learning proposal](/home/yc54616/Desktop/ctfh-core/docs/reviews/v3/REVIEW_BUNDLE.md:599)

`memory=off` must also isolate or disable native-agent memory and indexes. `memory=frozen` requires immutable content, not merely a backend name. `memory=live` cannot be reproducible from its mode label alone; record the revisions and material actually consumed.

Freeze learning inputs before evaluation. Repeatedly using one “held-out” set for promotion makes it a development set. Maintain an untouched final evaluation partition and exclude evaluator secrets and task-specific answers from worker-visible learning artifacts.

Native trace coverage may be incomplete. Record missing observations and unavailable counters rather than inventing a complete trajectory.

## PERFORMANCE FAST PATH

The default path should remain:

`admission → prepared environment → native AgentAdapter → immutable candidate → OutcomeAuthority → committed outcome`

No mandatory CTFH terminal wrapper, per-turn compiler, tool-router hop, knowledge retrieval, or extra model call.

- Prepare immutable configuration and content digests once; avoid rediscovery on every action.
- Keep bulk output outside the kernel inbox and SQLite. Bound disk capture and declare overflow behavior.
- Keep candidate, cancellation, and terminal-state notifications deliverable during output floods.
- Verify candidates promptly; do not wait for full transcript hashing or slow cleanup.
- Perform bulk sealing/hashing outside KernelLoop.
- Release execution capacity after confirmed reclamation; perform slower cleanup separately.

Use three distinct comparisons:

1. **Direct native versus thin supervision:** identical agent-facing configuration and tool semantics.
2. **Thin supervision versus one capability addition:** measure PTY, browser, or another execution-interface change.
3. **Baseline versus one knowledge treatment:** frozen skills, wiki retrieval, or context transformation.

The proposed direct-native versus V3-plus-TerminalHand comparison cannot isolate wrapper overhead. [V3 benchmark](/home/yc54616/Desktop/ctfh-core/docs/reviews/v3/REVIEW_BUNDLE.md:1089)

Retain V2’s matched-configuration gate. Report cold and warm startup, queue delay, time to authoritative result, deadline completion including failures, cancellation latency, leaks, and event-level contention. Predeclare an overhead budget and acceptable completion regression margin. Token savings alone are insufficient. [Matched baseline](/home/yc54616/Desktop/ctfh-core/docs/reviews/v3/REVIEW_BUNDLE.md:2435)

## MIGRATION ORDER

1. **Establish the existing native-agent baseline.** The bundle’s test count does not demonstrate that this gate has passed.
2. **Fix lifecycle barriers first.** Exercise cancellation during provisioning and lazy acquisition, surviving descendants, restart reconciliation, target reset, partial artifacts, and output flooding.
3. **Make the smallest AgentAdapter/RunLock changes.** Preserve native behavior and validate effective configuration. Avoid package-wide relocation.
4. **Add interactive terminal support only where native facilities fall short.** Measure it independently.
5. **Add one required browser implementation under environment ownership.** Start with private managed state; introduce shared-profile handling separately.
6. **Configure frozen handwritten skills through native discovery.** Evaluate each treatment without a new SkillRuntime.
7. **Introduce frozen wiki consumption, then offline learning.** Generalize protocols only after additional real implementations expose replacement pressure.

Replace V3.0-A’s interface inventory with this sequence. Move trace-integrity work before capability and learning experiments.

## TOP 5 CONCRETE CHANGES

1. **Retain AgentAdapter; remove mandatory BrainRuntime, ContextCompiler, and ToolRouter from native execution.**
2. **Remove core universal Hand leases; place terminal/browser/GUI session controllers under WorkerEnvironment ownership.**
3. **Rewrite cancellation and reset barriers around confirmed termination followed by final sweep; admit no replacement while old holders can still act.**
4. **Use RunLock to freeze effective permissions, capabilities, skills, memory policy, and browser configuration; reject guarantees the selected implementation cannot enforce.**
5. **Run the matched real-native supervisor baseline before tool extraction; benchmark each capability and learning feature as a separate treatment.**