## VERDICT

**NEEDS-CHANGES.**

V3 identifies useful concepts, but turns too many of them into mandatory runtime interfaces. A supervisor that composes reasoning, context, tools, skills, memory, and interaction sessions is already approaching the general-purpose agent framework the proposal intends to avoid.

Retain the existing **AgentAdapter + WorkerEnvironment + TargetProvider + OutcomeAuthority** architecture. Introduce smaller implementation components inside those boundaries when a demonstrated need justifies them.

The most consequential contradiction is explicit: V3 promises stale-generation rejection, while the existing architecture correctly states that a database generation cannot revoke an open browser socket or process handle. The proposal supplies no enforcement mechanism that resolves this. [V3 leases](/home/yc54616/Desktop/ctfh-core/docs/reviews/v3/REVIEW_BUNDLE.md:372), [existing fencing constraint](/home/yc54616/Desktop/ctfh-core/docs/reviews/v3/REVIEW_BUNDLE.md:2156)

This review covers only the frozen bundle. Its reported **54 passing tests** do not establish V3 correctness or completion of the real-native-agent performance gate. These are design findings, not findings about uninspected implementation.

## CRITICAL CHANGES BEFORE IMPLEMENTATION

1. **Restore WorkerEnvironment as the resource-lifetime boundary.**
   The proposed layout names Brain, Hand, Tool, Memory, and Skill protocols but omits an explicit WorkerEnvironment protocol; the migration map splits the existing environment into kernel/runtime responsibilities and TerminalHand. That obscures the boundary carrying containment, hard kill, provisioning cancellation, and orphan cleanup. Kernel owns lifetime decisions; WorkerEnvironment implements them; interaction backends manage child resources within that lifetime. [Proposed layout and migration](/home/yc54616/Desktop/ctfh-core/docs/reviews/v3/REVIEW_BUNDLE.md:891)

2. **Preserve the native AgentAdapter contract.**
   The proposed BrainAdapter omits explicit effective configuration, cancellation, output sink, and visibility/enforcement declarations present in the existing contract. A complete native agent already controls context, tool selection, skill activation, and its internal loop. Mandatory ContextCompiler and ToolRegistry mediation would either duplicate those functions or promise control the adapter cannot deliver. Retain the existing contract and make additional integration adapter-specific. [Existing contract](/home/yc54616/Desktop/ctfh-core/docs/reviews/v3/REVIEW_BUNDLE.md:2011), [proposed replacement](/home/yc54616/Desktop/ctfh-core/docs/reviews/v3/REVIEW_BUNDLE.md:726)

3. **Replace universal Hand leases with enforceable ownership.**
   Attempt-private resources need owner attribution, containment, cancellation, and cleanup—not an independent lease subsystem. Shared resources need a specific broker that can reject stale operations at the actual I/O boundary. Lease expiry must never mean “capacity is free” while the previous holder can still act.

4. **Complete acquisition, cancellation, and restart semantics before adding sessions.**
   The illustrative Hand protocol does not establish bounded acquisition, cancellation during acquisition, or explicitly owner-scoped sweeping. Preserve the existing barrier: acquisition must become terminal, late-created resources must be contained and stopped, and capacity remains reserved until execution has ceased. Restart marks attempts LOST and reconciles resources; it does not transparently resume their browser or terminal actions. [Hand protocol](/home/yc54616/Desktop/ctfh-core/docs/reviews/v3/REVIEW_BUNDLE.md:394), [existing cancellation barrier](/home/yc54616/Desktop/ctfh-core/docs/reviews/v3/REVIEW_BUNDLE.md:2126)

5. **Resolve lazy capability acquisition against admission and RunLock.**
   The browser path acquires a lease after the attempt is running, while existing admission is all-or-nothing. Waiting for scarce browser capacity while retaining other reservations can stall progress. Predeclare the backend/configuration and reserve scarce capacity at admission; defer process startup. Optional late acquisition should be bounded and fail immediately when unavailable. It must not silently change the admitted profile. [Browser path](/home/yc54616/Desktop/ctfh-core/docs/reviews/v3/REVIEW_BUNDLE.md:812), [admission and RunLock](/home/yc54616/Desktop/ctfh-core/docs/reviews/v3/REVIEW_BUNDLE.md:2202)

6. **Carry forward outcome, artifact, and external-effect guarantees explicitly.**
   OutcomeAuthority supplies the authoritative verdict; KernelLoop alone commits SOLVED. Preserve sealed evidence, target-dependent freshness, compare-and-swap, and UNKNOWN external-effect reconciliation. Calling a Tool an “atomic typed operation” does not make its effects transactional or safely repeatable. A timed-out call may already have changed external state. [Existing outcome and journal semantics](/home/yc54616/Desktop/ctfh-core/docs/reviews/v3/REVIEW_BUNDLE.md:2077)

## KEEP

- Single KernelLoop, single authoritative writer, bounded inbox, and command dispatch after committed state transitions.
- Separate target and worker ownership; worker cancellation must not reset or destroy targets.
- Independent authoritative verification.
- Native agents as complete deployed systems, with effective configuration and visibility limitations recorded.
- Portable Agent Skills, progressive disclosure, and no permissions granted by skill content.
- Separate raw evidence, derived knowledge, and promoted procedural packages.
- Offline learning, immutable run configuration, lazy optional dependencies, and matched performance experiments.
- Direct internal calls and MCP where an interoperability boundary actually requires it.

These are already stronger architectural anchors than the proposed six-module taxonomy.

## MERGE OR DELETE

| Proposal | Recommended disposition |
|---|---|
| `BrainAdapter` | Merge into the existing `AgentAdapter`. “Brain” can remain explanatory vocabulary. |
| Mandatory `ContextCompiler` | Reduce to adapter-specific startup/configuration assembly. Custom API agents may have an internal compiler; native agents retain their own context management. |
| Core `ToolRegistry` / `ToolProvider.invoke` | Move tool exposure and invocation into the adapter or environment implementation. Avoid a second universal execution path. |
| Core `SkillRegistry` / `SkillRuntime` | Use native skill discovery and activation where supported. Share a small package-validation helper only when needed. |
| Universal `Hand` lifecycle ABI | Keep resource-specific session helpers below WorkerEnvironment. Extract a shared protocol after independent implementations demonstrate common requirements. |
| `RuntimeProfile` alongside HarnessProfile/RunLock | Merge overlapping configuration concepts. Preserve one user-facing profile and one immutable resolved configuration. |
| Separate TraceStore service | Reuse the artifact/trace storage path. Separate evidence semantics do not require another service or database. |
| Manifest for every internal component | Record actual deployed versions, configuration, and digests. Defer a universal module dependency/permission manifest. |

Delete the mandatory Tool Router from the native-agent architecture diagram. Optionality in prose does not compensate for placing it on the central execution path.

## DEFER

- Dynamic plugin discovery and a generalized module dependency resolver.
- Universal leases, lease renewal, and transparent session migration.
- Core memory search/write/revise interfaces.
- Vector/graph memory, Letta-style runtime integration, and automatic retrieval.
- Automated Wiki maintenance and skill proposal pipelines.
- Planner/executor splits, critics, model routing, and delegation overlays.
- Generic DesktopHand, GUI grounding, and real-user-profile sharing unless an immediate requirement demands them.
- Generic event buses, global ordering of every tool event, and distributed scheduling.

Do not build interfaces for all of these merely to keep future options open. The existing adapter and environment boundaries already provide substantial room for replacement.

## CORRECTED MODULE BOUNDARIES

| Boundary | Owns | Must not absorb |
|---|---|---|
| **CTFH Kernel** | Task/attempt state, admission, deadlines, reservations, lifecycle commands, ownership metadata, recovery coordination, RunLock, authoritative outcome commit | Model turns, tool routing, browser observations, skill activation, memory retrieval |
| **TaskSource** | Frozen task specifications, input references, evaluator identity | Agent or resource lifecycle |
| **TargetProvider** | Target provisioning/reconciliation/reset, target resource ownership, epoch transitions coordinated with Kernel | Worker cleanup |
| **WorkerEnvironment** | Workspace, isolation, process containment, installed capabilities, child terminal/browser/GUI resources, bounded create/kill/destroy/sweep | Reasoning, task success decisions |
| **AgentAdapter** | Native launch/configuration, supported capability integration, result extraction, cancellation cooperation, observable usage and trace reporting | Authoritative state writes or resources surviving environment termination |
| **OutcomeAuthority** | Verification protocol, authoritative/advisory verdict, external submission reconciliation where applicable | Direct task-state mutation |
| **Artifact/trace storage** | Append-only attempt output, sealing, publication, provenance, coverage limitations | Memory interpretation or agent context policy |
| **Offline learning tooling** | Trace analysis, Wiki revisions, candidate skills, evaluations, publication of versioned snapshots | Live attempt mutation or authoritative task state |

**Tool ownership is hybrid only at configuration time:** core records the admitted environment/adapter configuration; the environment supplies executable capabilities; the adapter exposes them using the native agent’s supported mechanisms. Core does not sit between every tool call and its implementation.

Capability requirements constrain selection. They do not grant permissions. Hard restrictions must be enforced by the environment or a mediating service, and unsupported enforcement requirements must cause admission failure.

## HANDS / BROWSER / TERMINAL REVIEW

**Hands are useful implementation vocabulary, but insufficient as a universal lifecycle abstraction.** A subprocess, an attached user browser, and an isolated desktop have different ownership and teardown rules. Resource-specific contracts should state those differences instead of hiding them behind `close()`.

**Terminal**

- Keep ordinary native shell/file operations on the native path.
- Add interactive sessions inside WorkerEnvironment when the native integration lacks required behavior.
- Define process state separately from command timeout: timeout does not imply termination.
- Use bounded reads with cursors, truncation information, and artifact references. A PTY may merge stdout and stderr; the proposed separate-tail observation cannot be universally promised.
- Preserve an independent command channel when an interactive session is occupied.
- Session identity should establish attribution, not imply crash-resumable execution.

The cited interactive-interface research motivates usable sessions; it does not establish that extracting a core TerminalHand improves this particular native integration.

**Browser**

- Exactly one component owns each browser session’s lifecycle, subordinate to the environment’s lifetime policy.
- Distinguish owned browsers from borrowed connections. Releasing a borrowed user profile must not mean killing the user’s browser or deleting its profile.
- Bind actions to the correct session/page and observation revision. A valid lease generation does not make an old element identifier current.
- Serialize incompatible mutations on the same page/session.
- Backend fallback must be explicit; authenticated state must not silently move between implementations.
- Verify how the selected native agent discovers the browser interface. V3 assumes tools can be registered during execution without establishing that the native adapter supports this.

**GUI**

Keep display ownership, focus, screenshots, coordinate grounding, and input serialization inside an isolated environment/backend. CTFH should see availability, resource cost, health, and teardown results. A native computer-use agent may already provide the necessary execution interface; wrapping it in another mandatory action loop needs evidence.

**Fencing and recovery**

For unmediated handles, stop the old holder before reassignment. Apply the same reasoning to target reset: block affected admissions and quiesce old users before allowing actions against the replacement epoch. A new integer in SQLite cannot stop already-issued I/O.

## MEMORY / SKILLS / WIKISKILL REVIEW

The proposed separation is sound as an information model:

- **Trace:** captured observations and actions, with provenance and coverage.
- **Memory/Wiki:** derived claims and remembered experience.
- **Skill:** a versioned procedural package.
- **Control state:** authoritative execution state, separate from all three.

They do not need four independent storage engines or services.

**Preserve the Wiki after rejected skill changes, but distinguish retained history from trusted knowledge.** A failed proposal can remain as an experiment record. Its assumptions must not automatically become retrieval-visible guidance for future attempts. Track source traces, applicability, validation status, and rejection reasons. Promote the retrieval-visible Wiki snapshot independently from the skill package. [Proposed persistence rule](/home/yc54616/Desktop/ctfh-core/docs/reviews/v3/REVIEW_BUNDLE.md:599)

**Freeze the entire learning input surface.** `memory=off` must also account for native persistent memory, indexes, prior workspaces, and browser history. `memory=frozen` must identify the actual snapshot and retrieval configuration. Pin skills, scripts, references, and requirements together. A named mode alone is not reproducibility. [Memory modes](/home/yc54616/Desktop/ctfh-core/docs/reviews/v3/REVIEW_BUNDLE.md:665)

**Keep learning writes out of acting agents initially.** Read-only mounted knowledge and native skill directories are sufficient first integrations. An explicit memory-write tool does not itself establish provenance, correctness, or permission to publish shared knowledge.

**Separate experimentation from final promotion.** Repeatedly selecting candidates against the same “held-out” set turns that set into development data. Use separate final promotion evidence; include regressions, failures, latency, and configuration compatibility. A deterministic verifier can establish an observed task outcome, not the general validity of every derived lesson.

**Capture evidence before introducing learning.** The proposal schedules TraceStore under Memory/Wiki, after multiple runtime changes. Evidence capture is foundational measurement infrastructure. Retain the existing attempt trace/artifact path from the first experiment, including explicit gaps where native tools hide output. [Implementation sequence](/home/yc54616/Desktop/ctfh-core/docs/reviews/v3/REVIEW_BUNDLE.md:1005)

## PERFORMANCE FAST PATH

The default path should remain:

**Resolve immutable configuration → admit → prepare environment → run native agent → seal evidence → verify → commit outcome.**

During native execution, ordinary tool calls should require no additional kernel round trip.

- **No compulsory per-turn work:** context recompilation, registry reconstruction, schema translation, memory retrieval, lease renewal, or extra model calls.
- **Lazy startup without hidden waiting:** reserve genuinely scarce capabilities before execution; start their processes only when needed.
- **Separate control traffic from telemetry:** lifecycle changes and candidates reach the bounded control inbox; bulk tool observations go directly to attempt output. Do not require a globally sequenced kernel event for every native action.
- **Bound output handling:** output floods and trace-storage exhaustion need explicit behavior and must not block cancellation or the authoritative writer. Record any evidence loss.
- **Measure all elapsed-time contributors:** configuration resolution, environment preparation, agent startup, browser startup, execution, sealing/hashing, verification, and capacity reclamation. Offline learning also needs resource isolation if it runs concurrently on the same host.

The proposed **direct native versus V3 + TerminalHand** comparison combines wrapper overhead with a changed execution interface. First measure the lifecycle wrapper with unchanged native tools and prompts. Then evaluate TerminalHand or observation transformations as separate treatments. [V3 comparison](/home/yc54616/Desktop/ctfh-core/docs/reviews/v3/REVIEW_BUNDLE.md:1089), [existing matched-baseline requirements](/home/yc54616/Desktop/ctfh-core/docs/reviews/v3/REVIEW_BUNDLE.md:2435)

Report completion by deadline, completion-over-time curves, failures/timeouts, and latency distributions—not solved-only averages. Define acceptable overhead and regression limits before trials; V3 currently provides metrics but no quantified performance acceptance budget.

## MIGRATION ORDER

1. **Reconcile the design with existing invariants.**
   Preserve I1–I14 explicitly. Resolve resource ownership, native integration, cancellation, and actual enforcement before reorganizing packages.

2. **Establish the unchanged native baseline.**
   Demonstrate the real end-to-end gate and matched effective configurations. Treat the frozen test count as historical information, not substitute evidence.

3. **Harden the existing contracts and fault coverage.**
   Cover delayed acquisition after cancellation, restart during acquisition, stale handles, target reset with active workers, partial artifacts, ambiguous external effects, and output floods. Keep this within the current architecture.

4. **Add one demonstrated environment capability.**
   If interactive sessions are missing, implement them behind WorkerEnvironment and the existing AgentAdapter. Benchmark before generalizing the interface.

5. **Integrate native skills and one optional managed browser backend.**
   Treat them as separate changes and experiments. Pin configuration and package contents; retain native activation where available.

6. **Evaluate a small frozen knowledge snapshot.**
   Use simple files and explicit provenance. Compare against genuinely isolated memory-off runs.

7. **Add offline Wiki/skill evolution only after retrieval shows value.**
   Establish independent publication, rollback, and final promotion evaluation. Generalize protocols only when another real implementation requires them.

Do not begin with the proposed seven-interface “interfaces only” milestone. Passing old tests after adding unused abstractions establishes little about boundary correctness or performance.

## TOP 5 CONCRETE CHANGES

1. **Keep `AgentAdapter`; remove mandatory Brain/ContextCompiler/ToolRegistry mediation from the native path.**
2. **Keep every attempt-owned session below WorkerEnvironment; remove universal Hand leases from the first implementation.**
3. **Rewrite capability admission around actual enforcement, immutable configuration, and reservation-before-use for scarce resources.**
4. **Make the existing cancellation, recovery, outcome, and artifact invariants explicit acceptance tests for every new environment capability.**
5. **Benchmark the unchanged native path before extraction; preserve traces immediately and introduce Wiki/skill learning later through frozen, independently promoted artifacts.**